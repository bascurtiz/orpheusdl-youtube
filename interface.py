"""
YouTube module interface for OrpheusDL.
Uses yt-dlp for downloading and searching YouTube content.
"""

import os
import re
from pathlib import Path
from typing import Optional, Dict, Any

from utils.models import (
    ModuleInformation, ModuleModes, ManualEnum, ModuleController,
    MediaIdentification, DownloadTypeEnum, SearchResult, Tags,
    TrackInfo, TrackDownloadInfo, DownloadEnum, CodecEnum,
    AlbumInfo, PlaylistInfo, ArtistInfo, QualityEnum, CodecOptions
)
from .youtube_api import YouTubeAPI, parse_youtube_url


module_information = ModuleInformation(
    service_name='YouTube',
    module_supported_modes=ModuleModes.download,
    session_settings={
        'cookies_path': './config/youtube-cookies.txt',
        'download_pause_seconds': 5,
        'download_mode': 'sequential',
    },
    global_settings={},
    netlocation_constant=['youtube', 'youtu.be'],
    test_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    url_decoding=ManualEnum.manual,
    login_behaviour=ManualEnum.manual
)


class ModuleInterface:
    def __init__(self, module_controller: ModuleController):
        self.exception = module_controller.module_error
        self.module_controller = module_controller
        settings = module_controller.module_settings
        
        # Get cookies path from settings
        cookies_path = settings.get('cookies_path', '')
        if not cookies_path:
            cookies_path = './config/youtube-cookies.txt'
            
        if cookies_path and not os.path.isabs(cookies_path):
            if cookies_path.startswith('./') or cookies_path.startswith('.\\'):
                # Relative to app root (CWD)
                cookies_path = os.path.abspath(cookies_path)
            else:
                # Make relative paths relative to config directory (legacy behavior)
                config_dir = os.path.join(module_controller.data_folder, '..', 'config')
                cookies_path = os.path.join(config_dir, cookies_path)
        
        # Get FFmpeg path from global settings
        ffmpeg_path = None
        if module_controller.orpheus_options:
            # Try to get FFmpeg path from OrpheusDL settings
            try:
                import json
                settings_path = os.path.join(module_controller.data_folder, '..', 'config', 'settings.json')
                if os.path.isfile(settings_path):
                    with open(settings_path, 'r') as f:
                        all_settings = json.load(f)
                        ffmpeg_path = all_settings.get('global', {}).get('advanced', {}).get('ffmpeg_path')
            except Exception:
                pass
        
        self.api = YouTubeAPI(
            cookies_path=cookies_path,
            ffmpeg_path=ffmpeg_path,
            sleep_interval=settings.get('download_pause_seconds', 5)
        )
        

        
    def custom_url_parse(self, link: str) -> Optional[MediaIdentification]:
        """Parse YouTube URL and determine media type."""
        parsed = parse_youtube_url(link)
        
        if not parsed:
            return None
        
        url_type = parsed['type']
        url_id = parsed['id']
        
        if url_type == 'video':
            return MediaIdentification(
                media_type=DownloadTypeEnum.track,
                media_id=url_id,
                extra_kwargs={'data': {url_id: None}}
            )
        elif url_type == 'playlist':
            return MediaIdentification(
                media_type=DownloadTypeEnum.playlist,
                media_id=url_id,
                extra_kwargs={'data': {url_id: None}}
            )
        elif url_type == 'channel':
            return MediaIdentification(
                media_type=DownloadTypeEnum.artist,
                media_id=url_id,
                extra_kwargs={'data': {url_id: None}}
            )
        
        return None
    
    def search(self, query_type: DownloadTypeEnum, query: str, tags: Tags = None, limit: int = 10):
        """Search YouTube for videos, playlists, or channels."""
        
        # Map query type to YouTube search type
        if query_type == DownloadTypeEnum.track:
            search_type = 'video'
        elif query_type == DownloadTypeEnum.playlist:
            search_type = 'playlist'
        elif query_type == DownloadTypeEnum.artist:
            search_type = 'channel'
        elif query_type == DownloadTypeEnum.album:
            # YouTube doesn't have albums, search for playlists instead
            search_type = 'playlist'
        else:
            raise self.exception(f'Query type {query_type.name} is not supported')
        
        results = self.api.search(query, search_type, limit)
        
        def _year_from_upload_date(upload_date):
            if not upload_date:
                return None
            s = str(upload_date)
            return s[:4] if len(s) >= 4 else None

        def _name_for_result(result):
            # For channel/artist search, show channel name only in Artist column (leave Title blank)
            if query_type == DownloadTypeEnum.artist:
                return ''
            return result.get('title', 'Unknown')

        def _channel_or_artist_for_result(result):
            # Prefer channel/uploader name; for playlists yt-dlp often doesn't provide these (would need extra fetch)
            return result.get('uploader') or result.get('channel') or result.get('title', 'Unknown')

        def _artists_for_result(result):
            # For playlists, don't show "Unknown" when channel isn't available (avoids slow per-result fetch)
            if query_type == DownloadTypeEnum.playlist:
                name = _channel_or_artist_for_result(result)
                if not name or (name or '').strip() == 'Unknown':
                    return []
            return [_channel_or_artist_for_result(result)]

        def _additional_for_result(result):
            if query_type != DownloadTypeEnum.playlist:
                return None
            n = result.get('playlist_count')
            if n is None:
                return None
            return [f"1 track" if n == 1 else f"{n} tracks"]

        def _skip_playlist_no_tracks(result):
            if query_type != DownloadTypeEnum.playlist:
                return False
            # Only hide playlists that explicitly have 0 entries; show when count is missing (yt-dlp may omit it in search)
            n = result.get('playlist_count')
            return n is not None and n == 0

        return [
            SearchResult(
                result_id=result['id'],
                name=_name_for_result(result),
                artists=_artists_for_result(result) or None,
                duration=result.get('duration'),
                year=_year_from_upload_date(result.get('upload_date')),
                additional=_additional_for_result(result),
                image_url=result.get('thumbnail'),
                extra_kwargs={'data': {result['id']: result}}
            )
            for result in results
            if not _skip_playlist_no_tracks(result)
        ]
    
    def _parse_title_artist(self, title: str, uploader: str) -> tuple[str, str]:
        """
        Attempt to extract artist and title from the video title.
        Returns (artist, title).
        """
        # Regex to match "Artist - Title" patterns (handling hyphen, en-dash, colon)
        # We look for a separator surrounded by whitespace
        match = re.search(r"^(?P<artist>.+?)\s+[-:–]\s+(?P<title>.+)$", title)
        
        if match:
            extracted_artist = match.group('artist').strip()
            extracted_title = match.group('title').strip()
            
            # Check if the extracted artist is related to the uploader
            # This prevents false positives where the title structure mimics "Artist - Title" but isn't
            # e.g. "Review - Some Product" where uploader is "TechReviewer"
            # But we want to catch "Anne-Marie" in "Anne-Marie - Alarm" where uploader is "Anne-Marie"
            if uploader.lower() in extracted_artist.lower() or extracted_artist.lower() in uploader.lower():
                return extracted_artist, extracted_title
                
        # Fallback: use uploader as artist and original title
        return uploader, title

    def _clean_title(self, title: str) -> str:
        """Clean unwanted tags from video title."""
        if not title: return title
        
        # Step 1: Replace en-dash with hyphen
        title = title.replace('–', '-')
        
        # Step 2: Remove hashtags and everything after them
        # Match # followed by word characters, remove from first hashtag to end
        title = re.sub(r'\s*#\w+.*$', '', title)
        
        # List of tags to remove (case insensitive)
        # We match these inside (), [], {}, ||, -, ., /, •, +
        tags = [
            # Video & Visual
            r"Official Video", r"Official Music Video", r"Official Lyric Video",
            r"Music Video", r"Lyric Video", r"Video Oficial", r"Videoclip Oficial",
            r"Official", r"Video", r"Pseudo Video", r"Visualizer", r"VISUALIZER",
            r"Official HD Video", r"Official 4K Video", r"Premiere", r"Visualizer Video",
            r"Official CantoYo Video", r"Official Video HD", r"Official Trailer",
            r"M/V", r"Cover Audio Video", r"Remastered Video", r"Acoustic Video",
            r"Offical Video", r"Visualiser", r"Animated Lyric Video",
            r"Official Video Remastered HD", r"Lyrics / Lyric Video",
            r"Official Live Video", r"Official Classic Version", r"Animated Video",
            r"Official Video 2016", r"Official Video 2021", r"Official Video HQ",
            r"Official Music Vidéo", r"VIDEO OFFICIAL", r"Official Vedio",
            r"\*\*OFFICIAL VIDEO\*\*", r"Pop-up Video",
            
            # Audio & Stream
            r"Audio", r"Official Audio", r"Audio Stream", r"Official Full Stream",
            r"Cover Art", r"Audio Only", r"Audio Officiel", r"Audio Oficial",
            
            # Quality & Technical
            r"HD", r"4K Remaster", r"Remastered \d+", r"Full HD Remastered",
            r"Best Quality", r"Ultra High Quality", r"60fps", r"98 BPM_G major",
            r"Stereo", r"HQ \+ Lyrics", r"HQ", r"HQ Remaster", r"wmv", r"30sec",
            r"720P", r"1080P", r"flv", r"mov", r"Full Version HD", r"in 4K",
            r"HQ HD Dirty", r"HD Widescreen Music Video",
            
            # Content & Metadata
            r"Explicit", r"UNCENSORED", r"Lyrics", r"Free", r"w/ Lyrics",
            r"Ultra Music", r"Spinnin Records", r"OUT NOW", r"OUT NOW!",
            r"YHLQMDLG", r"TopPop", r"LYRICS!!", r"Ringtone Download",
            r"New Single", r"English", r"FREE DOWNLOAD", r"LYRICS",
            r"with lyrics", r"with download link", r"DOWNLOAD AVAILABLE!",
            r"Original", r"original", r"Full Length", r"FULL", r"Lyrics Video",
            r"CDQ", r"New/CDQ/Dirty", r"on ITUNES NOW", r"Original Radio",
            r"Out Now!", r"DVD Cut", r"Lyriclizer", r"Official Version",
            r"WSHH Exclusive", r"WSHH Premiere", r"CLIPE OFICIAL",
            r"non-official recut", r"Dirty", r"Videoclip"
        ]
        
        # Construct regex pattern
        # Matches: ( [ { | - . / • +  tag  +  ) ] } | - . / • + or EndOfString
        # We use non-capturing groups for the tags joined by |
        tags_pattern = '|'.join(tags)
        pattern = r'\s*[\(\[\|\{\-\.\/•\+]\s*(' + tags_pattern + r')\s*(?:[\)\]\|\}\-\.\/•\+]|\s*$)'
        
        # Remove matches, ignoring case
        clean_title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        clean_title = re.sub(r'\s+', ' ', clean_title).strip()
        
        return clean_title

    def get_track_info(self, track_id: str, quality_tier: QualityEnum, codec_options: CodecOptions, data: Dict = None, **kwargs):
        """Get information about a YouTube video."""
        
        # Get video info from cache or API
        video_data = None
        if data and track_id in data:
            video_data = data[track_id]
        
        if not video_data:
            video_data = self.api.get_video_info(track_id)
        
        if not video_data:
            return TrackInfo(
                name='Unknown',
                album='YouTube',
                album_id='',
                artists=['Unknown'],
                tags=Tags(),
                codec=CodecEnum.OPUS,
                cover_url='',
                release_year=2024,
                error='Failed to get video information',
                id=track_id,
                sample_rate=48.0,
                preview_url=f"https://www.youtube.com/watch?v={track_id}" if track_id else None,
            )
        
        # Extract metadata
        raw_title = video_data.get('title', 'Unknown') or 'Unknown'
        
        # Clean title tags
        raw_title = self._clean_title(raw_title)
        
        raw_uploader = video_data.get('uploader', video_data.get('channel', 'Unknown')) or 'Unknown'
        
        # Remove " - Topic" suffix from uploader if present
        if raw_uploader and raw_uploader.endswith(' - Topic'):
            raw_uploader = raw_uploader[:-8]
        
        # Fallback to channel name if uploader is unknown
        if (raw_uploader == 'Unknown' or not raw_uploader) and kwargs.get('channel_name'):
            raw_uploader = kwargs['channel_name']
            
        # Parse artist and title from the video title to avoid redundancy
        # e.g. if title is "Anne-Marie - Alarm" and uploader is "Anne-Marie", we want artist="Anne-Marie", title="Alarm"
        # instead of artist="Anne-Marie", title="Anne-Marie - Alarm"
        uploader, title = self._parse_title_artist(raw_title, raw_uploader)
        duration = video_data.get('duration')
        thumbnail = video_data.get('thumbnail')
        upload_date = video_data.get('upload_date', '')
        
        # Parse upload date (format: YYYYMMDD)
        release_year = 2024
        release_date = None
        if upload_date and len(upload_date) >= 4:
            try:
                release_year = int(upload_date[:4])
                if len(upload_date) == 8:
                    release_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
            except ValueError:
                pass
        
        # Best available thumbnail
        thumbnails = video_data.get('thumbnails', [])
        if thumbnails:
            # Get highest quality thumbnail
            best_thumb = max(thumbnails, key=lambda t: t.get('width', 0) * t.get('height', 0))
            thumbnail = best_thumb.get('url', thumbnail)
        
        # Determine codec/format based on quality_tier (from global settings)
        # This allows the right-click context menu to override preferred_format
        # Mapping: HIFI -> opus, HIGH -> aac, LOW/MEDIUM/MINIMUM -> mp3
        quality_to_format = {
            QualityEnum.HIFI: 'opus',
            QualityEnum.LOSSLESS: 'opus',
            QualityEnum.HIGH: 'aac',
            QualityEnum.MEDIUM: 'aac',
            QualityEnum.LOW: 'mp3',
            QualityEnum.MINIMUM: 'mp3',
        }
        
        # Use quality_tier to determine format, fallback to opus
        selected_format = quality_to_format.get(quality_tier, 'opus')
        
        if selected_format == 'opus':
            codec = CodecEnum.OPUS
        elif selected_format == 'aac' or selected_format == 'm4a':
            codec = CodecEnum.AAC
        elif selected_format == 'mp3':
            codec = CodecEnum.MP3
        else:
            codec = CodecEnum.OPUS
        
        # YouTube delivers OPUS/MP3 (and typically AAC) at 48kHz; display matches actual output
        sample_rate = 48.0
        return TrackInfo(
            name=title,
            album='YouTube',
            album_id='youtube',
            artists=[uploader],
            artist_id=video_data.get('channel_id', ''),
            tags=Tags(
                release_date=release_date,
                genres=['YouTube'],
                description=video_data.get('description', '')[:500] if video_data.get('description') else None,
            ),
            codec=codec,
            cover_url=thumbnail or '',
            release_year=release_year,
            duration=duration,
            id=track_id,
            sample_rate=sample_rate,
            preview_url=f"https://www.youtube.com/watch?v={track_id}" if track_id else None,
            download_extra_kwargs={
                'video_id': track_id,
                'video_data': video_data,
                'selected_format': selected_format,
            }
        )
    
    def get_track_download(self, video_id: str = None, video_data: Dict = None, **kwargs):
        """Download audio from YouTube video."""
        
        if not video_id:
            video_id = kwargs.get('track_id')
        
        if not video_id:
            raise self.exception('No video ID provided')
        
        # Download to temp file
        # Use format from download_extra_kwargs if provided (from context menu selection)
        # Otherwise fall back to opus
        selected_format = kwargs.get('selected_format', 'opus')
        temp_path = self.api.download_audio_to_temp(video_id, preferred_codec=selected_format)
        
        if not temp_path or not os.path.isfile(temp_path):
            raise self.exception(f'Failed to download audio for video {video_id}')
        
        # Determine codec from file extension
        ext = os.path.splitext(temp_path)[1].lower()
        codec_map = {
            '.opus': CodecEnum.OPUS,
            '.mp3': CodecEnum.MP3,
            '.m4a': CodecEnum.AAC,
            '.aac': CodecEnum.AAC,
            '.webm': CodecEnum.OPUS,
            '.ogg': CodecEnum.VORBIS,
        }
        codec = codec_map.get(ext, CodecEnum.OPUS)
        
        return TrackDownloadInfo(
            download_type=DownloadEnum.TEMP_FILE_PATH,
            temp_file_path=temp_path,
            different_codec=codec
        )
    
    def get_playlist_info(self, playlist_id: str, data: Dict = None, **kwargs):
        """Get information about a YouTube playlist."""
        
        playlist_data = None
        if data and playlist_id in data:
            cached_data = data[playlist_id]
            # Only use cached data if it has entries (i.e. it's a full playlist object, not just a search result)
            if cached_data and cached_data.get('entries'):
                playlist_data = cached_data
        
        if not playlist_data:
            # Check if this is a channel ID (starts with UC) and convert to uploads playlist (UU)
            if playlist_id.startswith('UC'):
                uploads_id = 'UU' + playlist_id[2:]
                playlist_data = self.api.get_playlist_info(uploads_id)
            
            if not playlist_data:
                playlist_data = self.api.get_playlist_info(playlist_id)
        
        if not playlist_data:
            raise self.exception(f'Failed to get playlist info for {playlist_id}')
        
        # Extract video IDs
        entries = playlist_data.get('entries', [])
        track_ids = [entry['id'] for entry in entries if entry and entry.get('id')]
        track_data = {entry['id']: entry for entry in entries if entry and entry.get('id')}
        
        return PlaylistInfo(
            name=playlist_data.get('title', 'Unknown Playlist'),
            creator=playlist_data.get('uploader', playlist_data.get('channel', 'Unknown')),
            creator_id=playlist_data.get('channel_id', ''),
            tracks=track_ids,
            release_year=2024,
            cover_url=playlist_data.get('thumbnail', ''),
            description=playlist_data.get('description', ''),
            track_extra_kwargs={'data': track_data}
        )
    
    def get_album_info(self, album_id: str, data: Dict = None, **kwargs):
        """YouTube doesn't have albums, treat as playlist."""
        return self.get_playlist_info(album_id, data, **kwargs)
    
    def get_artist_info(self, artist_id: str, get_credited_albums: bool = True, data: Dict = None, **kwargs):
        """Get information about a YouTube channel."""
        
        channel_data = None
        if data and artist_id in data:
            channel_data = data[artist_id]
        
        # If we have channel data but it doesn't contain entries (videos), 
        # or if we don't have channel data at all, fetch it from the API
        if not channel_data or 'entries' not in channel_data:
            full_channel_data = self.api.get_channel_info(artist_id)
            if full_channel_data:
                channel_data = full_channel_data
        
        if not channel_data:
            raise self.exception(f'Failed to get channel info for {artist_id}')
        
        # Extract video IDs
        entries = channel_data.get('entries', [])
        track_ids = [entry['id'] for entry in entries if entry and entry.get('id')]
        track_data = {entry['id']: entry for entry in entries if entry and entry.get('id')}
        
        channel_name = channel_data.get('title', channel_data.get('uploader', 'Unknown'))
        return ArtistInfo(
            name=channel_name,
            artist_id=artist_id,
            albums=[],  # YouTube channels don't have albums
            tracks=track_ids,
            track_extra_kwargs={'data': track_data, 'channel_name': channel_name}
        )
    
    def get_preview_stream_url(self, track_id: str) -> Optional[str]:
        """
        Get a preview stream URL for a YouTube video using low-quality audio.
        This can be used for preview playback in the GUI.
        
        Returns the preview URL if available, None otherwise.
        """
        try:
            # Use yt-dlp to extract format information and get a low-quality audio stream URL
            video_info = self.api.get_video_info(track_id)
            
            if not video_info:
                return None
            
            # Get available formats
            formats = video_info.get('formats', [])
            if not formats:
                return None
            
            # Look for low-quality audio-only formats (prefer worstaudio for speed)
            # Format priority: audio-only with opus > aac > mp3 > any audio-only
            preferred_codecs = ['opus', 'aac', 'mp3']
            selected_format = None
            
            # First pass: try to find audio-only formats with preferred codecs
            for codec in preferred_codecs:
                for fmt in formats:
                    # Check if format is audio-only (no video codec) and matches codec
                    if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none' and \
                       fmt.get('acodec', '').startswith(codec):
                        # Prefer lower bitrate for faster loading - sort by bitrate
                        if selected_format is None:
                            selected_format = fmt
                        else:
                            # Compare bitrates and prefer lower
                            current_abr = selected_format.get('abr', 0) or selected_format.get('tbr', 0) or 0
                            fmt_abr = fmt.get('abr', 0) or fmt.get('tbr', 0) or 0
                            if fmt_abr < current_abr:
                                selected_format = fmt
            
            # If no preferred codec found, get any audio-only format (lowest bitrate)
            if not selected_format:
                audio_formats = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
                if audio_formats:
                    # Sort by bitrate (ascending) to get lowest quality for faster loading
                    audio_formats.sort(key=lambda x: x.get('abr', 0) or x.get('tbr', 0) or 0)
                    selected_format = audio_formats[0]
            
            if selected_format:
                # Get the URL from the format
                url = selected_format.get('url')
                if url:
                    return url
            
            return None
            
        except Exception as e:
            import logging
            logging.debug(f'{module_information.service_name}: Error getting preview URL for track {track_id}: {e}')
            return None