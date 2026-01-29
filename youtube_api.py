"""
YouTube API wrapper using yt-dlp for OrpheusDL.
Provides search, metadata extraction, and audio download functionality.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from urllib.parse import quote

# Lazy import yt-dlp to avoid issues with PyInstaller
yt_dlp = None
_cookie_warning_shown = False
_shown_warnings = set()

def _get_yt_dlp():
    """Lazily import yt-dlp module."""
    global yt_dlp
    if yt_dlp is None:
        import yt_dlp as _yt_dlp
        yt_dlp = _yt_dlp
    return yt_dlp


class YouTubeAPI:
    """Wrapper around yt-dlp for YouTube operations."""
    
    def __init__(self, cookies_path: Optional[str] = None, ffmpeg_path: Optional[str] = None, **kwargs):
        """
        Initialize YouTube API wrapper.
        
        Args:
            cookies_path: Path to cookies.txt file for authenticated requests
            ffmpeg_path: Path to FFmpeg binary for audio extraction
        """
        self.cookies_path = cookies_path
        self.ffmpeg_path = ffmpeg_path
        try:
            self.sleep_interval = int(kwargs.get('sleep_interval', 5))
        except (ValueError, TypeError):
            self.sleep_interval = 5
        
        # Check for ffmpeg availability on non-Windows platforms
        self._check_ffmpeg_availability()
    
    def _check_ffmpeg_availability(self):
        """Check if ffmpeg is available, show installation instructions if not."""
        import platform
        
        # Skip check on Windows (ffmpeg is bundled)
        if platform.system() == 'Windows':
            return
        
        # Check if ffmpeg path was provided and exists
        if self.ffmpeg_path and os.path.isfile(self.ffmpeg_path):
            return
        
        # Check if ffmpeg is in system PATH
        ffmpeg_in_path = shutil.which('ffmpeg')
        if ffmpeg_in_path:
            return
        
        # ffmpeg not found - show installation instructions
        system = platform.system()
        if system == 'Darwin':
            install_cmd = "brew install ffmpeg"
            print("[YouTube] WARNING: ffmpeg not found. Install with: " + install_cmd)
        elif system == 'Linux':
            install_cmd = "sudo apt install ffmpeg  (or use your distro's package manager)"
            print("[YouTube] WARNING: ffmpeg not found. Install with: " + install_cmd)
        else:
            print("[YouTube] WARNING: ffmpeg not found. Please install ffmpeg for audio extraction.")
        
    def _get_base_opts(self) -> Dict[str, Any]:
        """Get base yt-dlp options."""
        class YtDlpLogger:
            def debug(self, msg):
                # Suppress verbose debug output
                pass
            def info(self, msg):
                pass
            def warning(self, msg):
                # Filter out known noisy warnings
                if "No supported JavaScript runtime" in msg:
                    return
                if "web client https formats have been skipped" in msg:
                    return
                
                if "The provided YouTube account cookies are no longer valid" in msg:
                    global _cookie_warning_shown
                    if _cookie_warning_shown:
                        return
                    _cookie_warning_shown = True

                # Deduplicate warnings of the same nature
                # Strip [extractor] id: prefix to identify the core message
                clean_msg = msg
                # Pattern: [anything] anything: message
                match = re.match(r'^\[.*?\]\s+.*?:?\s+(.*)$', msg)
                if match:
                    clean_msg = match.group(1)
                
                global _shown_warnings
                if clean_msg in _shown_warnings:
                    return
                
                _shown_warnings.add(clean_msg)
                    
                print(f"[YouTube Warning] {msg}")
            def error(self, msg):
                print(f"[YouTube Error] {msg}")

        opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'logger': YtDlpLogger(),
            'sleep_interval': self.sleep_interval,
        }
            
        if self.ffmpeg_path:
            opts['ffmpeg_location'] = self.ffmpeg_path
            
        return opts

    @contextmanager
    def _managed_options(self) -> Dict[str, Any]:
        """
        Context manager that provides yt-dlp options with a temporary cookie file.
        This prevents race conditions where multiple threads try to write to the same cookie file.
        """
        opts = self._get_base_opts()
        temp_cookie_path = None
        
        try:
            if self.cookies_path and os.path.isfile(self.cookies_path):
                # Create a temporary copy of the cookies file
                # We use mkstemp to ensure we get a unique file path
                fd, temp_cookie_path = tempfile.mkstemp(suffix='.txt', prefix='yt_cookies_')
                os.close(fd)  # Close the file descriptor immediately, we just need the path
                
                # Copy the original cookies to the temp file
                shutil.copy2(self.cookies_path, temp_cookie_path)
                
                # Use the temp file for this operation
                opts['cookiefile'] = temp_cookie_path
            
            yield opts
            
        finally:
            # Cleanup: remove the temporary cookie file
            if temp_cookie_path and os.path.exists(temp_cookie_path):
                try:
                    os.remove(temp_cookie_path)
                except Exception as e:
                    print(f"[YouTube] Warning: Could not remove temp cookie file {temp_cookie_path}: {e}")
    
    def search(self, query: str, search_type: str = 'video', limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search YouTube for videos, playlists, or channels.
        
        Args:
            query: Search query string
            search_type: Type of search - 'video', 'playlist', or 'channel'
            limit: Maximum number of results
            
        Returns:
            List of search results with id, title, uploader, duration, etc.
        """
        _yt_dlp = _get_yt_dlp()
        
        # Build search URL based on type
        if search_type == 'playlist':
            # Use YouTube search URL with playlist filter (sp=EgIQAw%253D%253D)
            # ytsearchpl: prefix is not supported by yt-dlp
            search_url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAw%253D%253D"
        elif search_type == 'channel':
            # For channels, we search videos and extract unique channels
            search_url = f"ytsearch{limit * 2}:{query}"
        else:
            search_url = f"ytsearch{limit}:{query}"
        
        results = []
        
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True
                opts['playlist_items'] = f'1-{limit}'
                
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(search_url, download=False)
                    
                    if info and 'entries' in info:
                        entries = info['entries'] or []
                        
                        if search_type == 'channel':
                            # Extract unique channels
                            seen_channels = set()
                            for entry in entries:
                                if entry and entry.get('channel_id'):
                                    channel_id = entry['channel_id']
                                    if channel_id not in seen_channels:
                                        seen_channels.add(channel_id)
                                        # Try multiple possible fields for channel thumbnail
                                        thumbnail = (
                                            entry.get('thumbnail') or
                                            entry.get('channel_thumbnail') or
                                            entry.get('channel_follower_count') or
                                            (entry.get('thumbnails', [{}])[0].get('url') if entry.get('thumbnails') else None)
                                        )
                                        # Note: When extract_flat=True, channel thumbnails might not be included
                                        # If not available, the GUI will lazy-load them
                                        
                                        results.append({
                                            'id': channel_id,
                                            'title': entry.get('channel', entry.get('uploader', 'Unknown')),
                                            'url': f"https://www.youtube.com/channel/{channel_id}",
                                            'type': 'channel',
                                            'thumbnail': thumbnail,
                                        })
                                        if len(results) >= limit:
                                            break
                        else:
                            for entry in entries:
                                if entry:
                                    video_id = entry.get('id')
                                    thumbnail = entry.get('thumbnail')
                                    
                                    if search_type == 'playlist':
                                        # For playlists, try multiple possible fields for thumbnail
                                        if not thumbnail:
                                            thumbnail = (
                                                entry.get('playlist_thumbnail') or
                                                entry.get('thumbnails', [{}])[0].get('url') if entry.get('thumbnails') else None
                                            )
                                        # Note: When extract_flat=True, playlist thumbnails might not be included
                                        # If not available, the GUI will lazy-load them
                                    else:
                                        # For videos, fallback: construct thumbnail URL from video ID if not provided
                                        # YouTube thumbnails follow pattern: https://i.ytimg.com/vi/{VIDEO_ID}/default.jpg
                                        if not thumbnail and video_id:
                                            thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
                                    
                                    results.append({
                                        'id': video_id or entry.get('id'),
                                        'title': entry.get('title'),
                                        'uploader': entry.get('uploader', entry.get('channel', 'Unknown')),
                                        'channel_id': entry.get('channel_id'),
                                        'duration': entry.get('duration'),
                                        'url': entry.get('url', f"https://www.youtube.com/watch?v={video_id}" if video_id else f"https://www.youtube.com/playlist?list={entry.get('id')}"),
                                        'thumbnail': thumbnail,
                                        'type': 'playlist' if search_type == 'playlist' else 'video',
                                    })
        except Exception as e:
            print(f"[YouTube] Search error: {e}")
            
        return results
    
    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a video.
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            Dictionary with video metadata or None if failed
        """
        _yt_dlp = _get_yt_dlp()
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = False
                
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    return info
        except Exception as e:
            print(f"[YouTube] Error getting video info: {e}")
            return None
    
    def get_playlist_info(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a playlist including all video IDs.
        
        Args:
            playlist_id: YouTube playlist ID
            
        Returns:
            Dictionary with playlist metadata and video entries
        """
        _yt_dlp = _get_yt_dlp()
        
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True  # Don't download each video's info
                
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    # Playlist thumbnail might be in the info dict itself
                    if info and not info.get('thumbnail'):
                        # Try to get thumbnail from first entry if available
                        entries = info.get('entries', [])
                        if entries and entries[0] and entries[0].get('thumbnail'):
                            info['thumbnail'] = entries[0].get('thumbnail')
                    return info
        except Exception as e:
            print(f"[YouTube] Error getting playlist info: {e}")
            return None
    
    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a channel including recent uploads.
        
        Args:
            channel_id: YouTube channel ID
            
        Returns:
            Dictionary with channel metadata and video entries
        """
        _yt_dlp = _get_yt_dlp()
        
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True
                opts['playlist_items'] = '1-50'  # Limit to 50 videos
                
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    # Channel thumbnail might be in the info dict itself
                    # Try multiple possible fields
                    if info:
                        if not info.get('thumbnail'):
                            # Try alternative fields
                            thumbnail = (
                                info.get('channel_thumbnail') or
                                info.get('channel_follower_count') or
                                (info.get('thumbnails', [{}])[0].get('url') if info.get('thumbnails') else None)
                            )
                            if thumbnail:
                                info['thumbnail'] = thumbnail
                    return info
        except Exception as e:
            print(f"[YouTube] Error getting channel info: {e}")
            return None
    
    def get_channel_thumbnail(self, channel_id: str) -> Optional[str]:
        """
        Get channel thumbnail URL.
        Tries to fetch channel info to get the thumbnail.
        
        Args:
            channel_id: YouTube channel ID
            
        Returns:
            Thumbnail URL or None if not available
        """
        try:
            channel_info = self.get_channel_info(channel_id)
            if channel_info:
                # Try multiple possible fields for channel thumbnail
                thumbnail = (
                    channel_info.get('thumbnail') or
                    channel_info.get('channel_thumbnail') or
                    channel_info.get('channel_follower_count') or  # Sometimes thumbnail is here
                    (channel_info.get('thumbnails', [{}])[0].get('url') if channel_info.get('thumbnails') else None)
                )
                return thumbnail
        except Exception as e:
            print(f"[YouTube] Error getting channel thumbnail: {e}")
        return None
    
    def download_audio(self, video_id: str, output_path: str, preferred_codec: str = 'opus') -> Optional[str]:
        """
        Download audio from a YouTube video.
        
        Args:
            video_id: YouTube video ID
            output_path: Output file path (without extension)
            
        Returns:
            Path to downloaded file or None if failed
        """
        _yt_dlp = _get_yt_dlp()
        
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Construct format string based on preference
        # Fallback order: Preferred -> Opus -> AAC (M4A) -> MP3 -> Best Audio -> Best
        format_str = ""
        if preferred_codec == 'opus':
            format_str = 'bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio[acodec=mp3]/bestaudio/best'
        elif preferred_codec == 'mp3':
            format_str = 'bestaudio[acodec=mp3]/bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio/best'
        elif preferred_codec == 'm4a' or preferred_codec == 'aac':
            format_str = 'bestaudio[acodec=aac]/bestaudio[acodec=opus]/bestaudio[acodec=mp3]/bestaudio/best'
        else:
            format_str = 'bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio[acodec=mp3]/bestaudio/best'

        try:
            with self._managed_options() as opts:
                opts.update({
                    # Audio format preference with fallback
                    'format': format_str,
                    'outtmpl': output_path + '.%(ext)s',
                    'add_metadata': True,
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': preferred_codec if preferred_codec in ['mp3', 'opus', 'm4a', 'aac'] else 'opus',
                        'preferredquality': '192',
                    }],
                    'keepvideo': False,
                })
                
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                    
                # Find the downloaded file
                for ext in ['opus', 'mp3', 'm4a', 'webm', 'ogg']:
                    path = f"{output_path}.{ext}"
                    if os.path.isfile(path):
                        # Check if we got the preferred codec
                        if preferred_codec == 'm4a' or preferred_codec == 'aac': preferred_check = 'm4a' # m4a is usually aac in container
                        else: preferred_check = preferred_codec
                        
                        if ext != preferred_check and ext != preferred_codec:
                             # Map common extensions if needed
                             is_match = False
                             if (preferred_codec == 'm4a' or preferred_codec == 'aac') and ext == 'm4a': is_match = True
                             elif preferred_codec == 'opus' and ext == 'opus': is_match = True
                             elif preferred_codec == 'mp3' and ext == 'mp3': is_match = True
                             
                             if not is_match:
                                 print(f"[YouTube] Preferred format '{preferred_codec}' not available. Fallback to '{ext}'.")
                        
                        return path
                        
                return None
        except Exception as e:
            error_msg = str(e)
            cookies_location = self.cookies_path if self.cookies_path else "./config/youtube-cookies.txt"
            cookies_missing = not (self.cookies_path and os.path.isfile(self.cookies_path))
            
            # Check for age restriction errors
            if "Sign in to confirm your age" in error_msg or "age-restricted" in error_msg.lower():
                print(f"[YouTube] WARNING: Download failed due to age restriction.")
                print(f"[YouTube] Please ensure valid cookies are present at: {cookies_location}")
                print(f"[YouTube] You can export cookies using a browser extension (e.g., 'Get cookies.txt LOCALLY')")
            # Check for 403 Forbidden errors (bot detection / missing authentication)
            elif "403" in error_msg or "Forbidden" in error_msg:
                print(f"[YouTube] WARNING: Download failed with HTTP 403 Forbidden.")
                if cookies_missing:
                    print(f"[YouTube] This error typically occurs when YouTube detects automated access.")
                    print(f"[YouTube] Cookies are required to authenticate requests, even for non-age-restricted content.")
                    print(f"[YouTube] Please provide cookies at: {cookies_location}")
                    print(f"[YouTube] You can export cookies using a browser extension (e.g., 'Get cookies.txt LOCALLY')")
                else:
                    print(f"[YouTube] Cookies are present but may be invalid or expired.")
                    print(f"[YouTube] Please refresh your cookies file at: {cookies_location}")
            
            print(f"[YouTube] Download error: {e}")
            return None
    
    def download_audio_to_temp(self, video_id: str, preferred_codec: str = 'opus') -> Optional[str]:
        """
        Download audio to a temporary file.
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            Path to temporary file or None if failed
        """
        import tempfile
        
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, video_id)
        
        return self.download_audio(video_id, output_path, preferred_codec=preferred_codec)


def parse_youtube_url(url: str) -> Optional[Dict[str, str]]:
    """
    Parse a YouTube URL to extract the type and ID.
    
    Args:
        url: YouTube URL
        
    Returns:
        Dictionary with 'type' and 'id' keys, or None if invalid
    """
    # Video patterns
    video_patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    
    for pattern in video_patterns:
        match = re.search(pattern, url)
        if match:
            return {'type': 'video', 'id': match.group(1)}
    
    # Playlist pattern
    playlist_match = re.search(r'youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)', url)
    if playlist_match:
        return {'type': 'playlist', 'id': playlist_match.group(1)}
    
    # Also check for playlist in video URL
    playlist_in_video = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    if playlist_in_video:
        # If there's also a video ID, return the playlist
        return {'type': 'playlist', 'id': playlist_in_video.group(1)}
    
    # Channel patterns
    channel_patterns = [
        r'youtube\.com/channel/([a-zA-Z0-9_-]+)',
        r'youtube\.com/c/([a-zA-Z0-9_-]+)',
        r'youtube\.com/@([a-zA-Z0-9_-]+)',
    ]
    
    for pattern in channel_patterns:
        match = re.search(pattern, url)
        if match:
            return {'type': 'channel', 'id': match.group(1)}
    
    return None
