"""
YouTube API wrapper using yt-dlp for OrpheusDL.
Provides search, metadata extraction, and audio download functionality with JS runtime logging.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from urllib.parse import quote

# Lazy import yt-dlp to avoid PyInstaller issues
yt_dlp = None
_cookie_warning_shown = False
_shown_warnings = set()
_js_runtime_logged = False  # Runtime log guard


def _get_yt_dlp():
    """Lazily import yt-dlp module."""
    global yt_dlp
    if yt_dlp is None:
        import yt_dlp as _yt_dlp
        yt_dlp = _yt_dlp
    return yt_dlp


class YouTubeAPI:
    """Wrapper around yt-dlp for YouTube operations with runtime logging."""

    def __init__(self, cookies_path: Optional[str] = None, ffmpeg_path: Optional[str] = None, **kwargs):
        self.cookies_path = cookies_path
        self.ffmpeg_path = ffmpeg_path
        try:
            self.sleep_interval = int(kwargs.get('sleep_interval', 5))
        except (ValueError, TypeError):
            self.sleep_interval = 5
        self._check_ffmpeg_availability()

    def _check_ffmpeg_availability(self):
        import platform
        if platform.system() == 'Windows':
            return
        if self.ffmpeg_path and os.path.isfile(self.ffmpeg_path):
            return
        if shutil.which('ffmpeg'):
            return
        system = platform.system()
        if system == 'Darwin':
            print("[YouTube] WARNING: ffmpeg not found. Install with: brew install ffmpeg")
        elif system == 'Linux':
            print("[YouTube] WARNING: ffmpeg not found. Install with: sudo apt install ffmpeg")
        else:
            print("[YouTube] WARNING: ffmpeg not found. Please install ffmpeg for audio extraction.")

    def _get_base_opts(self) -> Dict[str, Any]:
        """Get base yt-dlp options with JS runtime detection (PyInstaller safe)."""
        global _js_runtime_logged, _cookie_warning_shown, _shown_warnings

        class YtDlpLogger:
            def debug(self, msg): self._detect_runtime(msg)
            def info(self, msg): self._detect_runtime(msg)
            def warning(self, msg):
                self._detect_runtime(msg)
                if "No supported JavaScript runtime" in msg: return
                if "web client https formats have been skipped" in msg: return
                if "The provided YouTube account cookies are no longer valid" in msg:
                    if _cookie_warning_shown: return
                    _cookie_warning_shown = True
                clean_msg = re.match(r'^\[.*?\]\s+.*?:?\s+(.*)$', msg)
                clean_msg = clean_msg.group(1) if clean_msg else msg
                if clean_msg in _shown_warnings: return
                _shown_warnings.add(clean_msg)
                print(f"[YouTube Warning] {msg}")
            def error(self, msg):
                print(f"[YouTube Error] {msg}")
            def _detect_runtime(self, msg):
                global _js_runtime_logged
                if _js_runtime_logged: return
                msg_l = msg.lower()
                if "using js runtime" in msg_l or ("deno" in msg_l and "js" in msg_l) or ("node" in msg_l and "js" in msg_l):
                    print(f"[YouTube] JS runtime detected: {msg}")
                    _js_runtime_logged = True
                elif "no supported javascript runtime" in msg_l:
                    print("[YouTube] JS runtime: builtin/fallback")
                    _js_runtime_logged = True

        opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': False,
            'logger': YtDlpLogger(),
            'sleep_interval': self.sleep_interval,
            # Enable EJS challenge solver script downloads from GitHub. Required when yt-dlp
            # is used as a library (PyInstaller/frozen) since EJS scripts are not bundled.
            # See https://github.com/yt-dlp/yt-dlp/wiki/EJS
            'remote_components': ['ejs:github'],
        }
        if self.ffmpeg_path:
            opts['ffmpeg_location'] = self.ffmpeg_path
        # JS runtime attempt: deno first
        try:
            if shutil.which("deno"):
                opts['js_runtime'] = 'deno'
            else:
                raise FileNotFoundError("deno not found in PATH")
        except Exception as e:
            opts.pop('js_runtime', None)
            print(f"[YouTube] JS runtime fallback to auto ({e})")
        return opts

    @contextmanager
    def _managed_options(self) -> Dict[str, Any]:
        opts = self._get_base_opts()
        temp_cookie_path = None
        try:
            if self.cookies_path and os.path.isfile(self.cookies_path):
                fd, temp_cookie_path = tempfile.mkstemp(suffix='.txt', prefix='yt_cookies_')
                os.close(fd)
                shutil.copy2(self.cookies_path, temp_cookie_path)
                opts['cookiefile'] = temp_cookie_path
            yield opts
        finally:
            if temp_cookie_path and os.path.exists(temp_cookie_path):
                try: os.remove(temp_cookie_path)
                except Exception as e:
                    print(f"[YouTube] Warning: Could not remove temp cookie file {temp_cookie_path}: {e}")

    def _thumbnail_from_entry(self, entry: Dict[str, Any], entry_id: Optional[str], search_type: str) -> Optional[str]:
        """Resolve thumbnail URL from a search result entry (video, playlist, or channel)."""
        # For channel search, entry.thumbnail/thumbnails are from the video result, not the channel avatar
        if search_type == 'channel':
            thumb = entry.get('channel_thumbnail')
            if not thumb and entry.get('thumbnails'):
                # Prefer avatar-shaped thumbnails (small square); video thumbs are typically 120x90 etc.
                thumbs = entry['thumbnails']
                if thumbs:
                    # Channel avatars are usually small and square (e.g. 48x48, 88x88, 176x176)
                    square = [t for t in thumbs if t.get('width') and t.get('height') and abs((t.get('width') or 0) - (t.get('height') or 0)) <= 16]
                    if square:
                        best = max(square, key=lambda t: (t.get('width') or 0) * (t.get('height') or 0))
                        thumb = best.get('url')
            if not thumb and entry_id:
                return self.get_channel_thumbnail(entry_id)
            return thumb or None
        thumb = entry.get('thumbnail')
        if not thumb and entry.get('thumbnails'):
            thumbs = entry['thumbnails']
            if thumbs:
                best = max(thumbs, key=lambda t: (t.get('width') or 0) * (t.get('height') or 0))
                thumb = best.get('url') or thumb
        if not thumb and entry.get('channel_thumbnail'):
            thumb = entry['channel_thumbnail']
        if not thumb and search_type == 'video' and entry_id:
            return f"https://i.ytimg.com/vi/{entry_id}/hqdefault.jpg"
        if not thumb and search_type == 'playlist' and entry_id:
            playlist_info = self.get_playlist_info(entry_id)
            if playlist_info and playlist_info.get('thumbnail'):
                return playlist_info['thumbnail']
        return thumb or None

    def search(self, query: str, search_type: str = 'video', limit: int = 10) -> List[Dict[str, Any]]:
        _yt_dlp = _get_yt_dlp()
        if search_type == 'playlist':
            search_url = f"https://www.youtube.com/results?search_query={quote(query)}&sp=EgIQAw%253D%253D"
        elif search_type == 'channel':
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
                            seen_channels = set()
                            for entry in entries:
                                if entry and entry.get('channel_id'):
                                    cid = entry['channel_id']
                                    if cid not in seen_channels:
                                        seen_channels.add(cid)
                                        thumb = self._thumbnail_from_entry(entry, cid, 'channel')
                                        results.append({
                                            'id': cid,
                                            'title': entry.get('channel', entry.get('uploader', 'Unknown')),
                                            'url': f"https://www.youtube.com/channel/{cid}",
                                            'type': 'channel',
                                            'thumbnail': thumb
                                        })
                                        if len(results) >= limit: break
                        else:
                            for entry in entries:
                                if entry:
                                    vid_id = entry.get('id')
                                    thumb = self._thumbnail_from_entry(entry, vid_id, search_type)
                                    out = {
                                        'id': vid_id or entry.get('id'),
                                        'title': entry.get('title'),
                                        'uploader': entry.get('uploader', entry.get('channel', 'Unknown')),
                                        'channel': entry.get('channel', entry.get('uploader', 'Unknown')),
                                        'channel_id': entry.get('channel_id'),
                                        'duration': entry.get('duration'),
                                        'upload_date': entry.get('upload_date'),
                                        'url': entry.get('url', f"https://www.youtube.com/watch?v={vid_id}" if vid_id else f"https://www.youtube.com/playlist?list={entry.get('id')}"),
                                        'thumbnail': thumb,
                                        'type': 'playlist' if search_type == 'playlist' else 'video',
                                    }
                                    if search_type == 'playlist':
                                        out['playlist_count'] = entry.get('playlist_count') or entry.get('n_entries')
                                    results.append(out)
        except Exception as e:
            print(f"[YouTube] Search error: {e}")
        return results

    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        _yt_dlp = _get_yt_dlp()
        url = f"https://www.youtube.com/watch?v={video_id}"
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = False
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"[YouTube] Error getting video info: {e}")
            return None

    def get_playlist_info(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        _yt_dlp = _get_yt_dlp()
        url = f"https://www.youtube.com/playlist?list={playlist_id}"
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and not info.get('thumbnail'):
                        entries = info.get('entries', [])
                        if entries and entries[0] and entries[0].get('thumbnail'):
                            info['thumbnail'] = entries[0].get('thumbnail')
                    return info
        except Exception as e:
            print(f"[YouTube] Error getting playlist info: {e}")
            return None

    def _is_avatar_url(self, url: str) -> bool:
        """True if URL looks like YouTube channel avatar. =s0 is the banner/full-size; avatar uses =s48, =s160, etc."""
        if not url or 'yt3.googleusercontent.com' not in url:
            return False
        # Reject =s0 (banner / full-size). Avatar has explicit size: =s48, =s88, =s100, =s160, =s176, =s200, ...
        if re.search(r'=s0(?:-|$|\?|/)', url):
            return False
        return bool(re.search(r'=s[1-9]\d+', url))

    def _channel_avatar_from_thumbnails(self, thumbnails: List[Dict[str, Any]]) -> Optional[str]:
        """Pick the channel avatar (small square) from thumbnails; avoid the banner (wide/large). Never return =s0."""
        if not thumbnails:
            return None
        # Only consider thumbnails that look like avatar URLs (=s48, =s160, etc.). Never use =s0 (banner).
        avatar_candidates = [t for t in thumbnails if t.get('url') and self._is_avatar_url(t.get('url'))]
        if not avatar_candidates:
            return None
        # Prefer small square (by dimensions) among avatar candidates
        square_small = [
            t for t in avatar_candidates
            if (t.get('width') or 0) <= 400 and (t.get('height') or 0) <= 400
            and abs((t.get('width') or 0) - (t.get('height') or 0)) <= 24
        ]
        if square_small:
            best = max(square_small, key=lambda t: (t.get('width') or 0) * (t.get('height') or 0))
            return best.get('url')
        return avatar_candidates[0].get('url')

    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        _yt_dlp = _get_yt_dlp()
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True
                opts['playlist_items'] = '1-50'
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info and not info.get('thumbnail'):
                        thumb = (
                            info.get('channel_thumbnail')
                            or self._channel_avatar_from_thumbnails(info.get('thumbnails') or [])
                            or (info.get('thumbnails', [{}])[0].get('url') if info.get('thumbnails') else None)
                        )
                        if thumb:
                            info['thumbnail'] = thumb
                    return info
        except Exception as e:
            print(f"[YouTube] Error getting channel info: {e}")
            return None

    def get_channel_thumbnail(self, channel_id: str) -> Optional[str]:
        """Fetch channel avatar (profile picture) via yt-dlp. Use channel root URL with
        playlist_items 0 so we get channel metadata only; thumbnail is then the avatar, not the banner."""
        _yt_dlp = _get_yt_dlp()
        # Channel root URL (no /videos) + playlist_items 0 => avatar as thumbnail (per yt-dlp docs)
        url = f"https://www.youtube.com/channel/{channel_id}"
        try:
            with self._managed_options() as opts:
                opts['extract_flat'] = True
                opts['playlist_items'] = '0'
                with _yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            if not info:
                return None
            # Prefer avatar only. Never use =s0 (banner/full-size). Every return guarded.
            thumb = self._return_avatar_only(info.get('channel_thumbnail'))
            if thumb:
                return thumb
            thumb = self._channel_avatar_from_thumbnails(info.get('thumbnails') or [])
            if thumb:
                return self._return_avatar_only(thumb) or None
            thumb = self._return_avatar_only(info.get('thumbnail'))
            if thumb:
                return thumb
            for t in info.get('thumbnails') or []:
                u = self._return_avatar_only(t.get('url') or '')
                if u:
                    return u
            return None
        except Exception as e:
            print(f"[YouTube] Error getting channel thumbnail: {e}")
        return None

    def _return_avatar_only(self, url: Optional[str]) -> Optional[str]:
        """Return URL only if it looks like an avatar (=s48, =s160, etc.). Never return =s0 (banner)."""
        return url if (url and self._is_avatar_url(url)) else None

    def download_audio(self, video_id: str, output_path: str, preferred_codec: str = 'opus') -> Optional[str]:
        _yt_dlp = _get_yt_dlp()
        url = f"https://www.youtube.com/watch?v={video_id}"
        fmt = {
            'opus': 'bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio[acodec=mp3]/bestaudio/best',
            'mp3': 'bestaudio[acodec=mp3]/bestaudio[acodec=opus]/bestaudio[acodec=aac]/bestaudio/best',
            'm4a': 'bestaudio[acodec=aac]/bestaudio[acodec=opus]/bestaudio[acodec=mp3]/bestaudio/best'
        }.get(preferred_codec, 'bestaudio/best')
        try:
            with self._managed_options() as opts:
                opts.update({
                    'format': fmt,
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
                for ext in ['opus', 'mp3', 'm4a', 'webm', 'ogg']:
                    path = f"{output_path}.{ext}"
                    if os.path.isfile(path): return path
                return None
        except Exception as e:
            msg = str(e)
            cookies_location = self.cookies_path if self.cookies_path else "./config/youtube-cookies.txt"
            if "Sign in to confirm your age" in msg or "age-restricted" in msg.lower():
                print(f"[YouTube] WARNING: Download failed due to age restriction. Use cookies at {cookies_location}")
            elif "403" in msg or "Forbidden" in msg:
                print(f"[YouTube] WARNING: Download failed with HTTP 403. Ensure cookies at {cookies_location}")
            print(f"[YouTube] Download error: {e}")
            return None

    def download_audio_to_temp(self, video_id: str, preferred_codec: str = 'opus') -> Optional[str]:
        temp_dir = tempfile.mkdtemp()
        output_path = os.path.join(temp_dir, video_id)
        return self.download_audio(video_id, output_path, preferred_codec=preferred_codec)


def parse_youtube_url(url: str) -> Optional[Dict[str, str]]:
    video_patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in video_patterns:
        m = re.search(pattern, url)
        if m: return {'type': 'video', 'id': m.group(1)}
    playlist_match = re.search(r'youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)', url)
    if playlist_match: return {'type': 'playlist', 'id': playlist_match.group(1)}
    playlist_in_video = re.search(r'[?&]list=([a-zA-Z0-9_-]+)', url)
    if playlist_in_video: return {'type': 'playlist', 'id': playlist_in_video.group(1)}
    channel_patterns = [
        r'youtube\.com/channel/([a-zA-Z0-9_-]+)',
        r'youtube\.com/c/([a-zA-Z0-9_-]+)',
        r'youtube\.com/@([a-zA-Z0-9_-]+)',
    ]
    for pattern in channel_patterns:
        m = re.search(pattern, url)
        if m: return {'type': 'channel', 'id': m.group(1)}
    return None
