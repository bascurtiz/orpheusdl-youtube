# YouTube Module for OrpheusDL

This module enables downloading audio from YouTube using OrpheusDL. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for downloading and searching.

## Features

- Download audio from YouTube videos
- Search YouTube for videos, playlists, and channels
- Support for playlists and channel downloads
- Cookie support for age-restricted content
- High-quality audio (Opus > M4A > MP3 with fallback)

## Prerequisites

### 1. OrpheusDL
You need [OrpheusDL](https://github.com/bascurtiz/orpheusdl) installed.

### 2. FFmpeg
FFmpeg is required for audio extraction. Make sure it's installed and either:
- In your system PATH
- Set in `config/settings.json` under `global.advanced.ffmpeg_path`

### 3. yt-dlp
You can install `yt-dlp` using pip:
```bash
pip install yt-dlp
```
Or download the binary from the [yt-dlp GitHub release page](https://github.com/yt-dlp/yt-dlp/releases) and place it in your system PATH or the module folder.

> **Note**: If you encounter "n challenge" or signature errors, you may need the latest master branch:
```bash
pip install -U https://github.com/yt-dlp/yt-dlp/archive/master.zip
```

### 4. Deno (Recommended)
Required for solving YouTube's latest "EJS" challenges

**Windows:**
1. Download `deno-x86_64-pc-windows-msvc.zip` from [Deno Releases](https://github.com/denoland/deno/releases).
2. Extract `deno.exe` to your OrpheusDL root folder (where `orpheus.py` is).

**macOS / Linux:**
1. Install via terminal:
   ```bash
   curl -fsSL https://deno.land/install.sh | sh
   ```
   *Or via Homebrew (macOS):* `brew install deno`
2. Ensure `deno` is in your system PATH, or symlink it to the OrpheusDL root folder.



## Installation

1. **Clone the module** into your OrpheusDL modules folder:
```bash
cd modules
git clone https://github.com/bascurtiz/orpheusdl-youtube youtube
```

2. **Run OrpheusDL** to generate settings:
```bash
python orpheus.py
```

## Usage

### Downloading

The module supports standard YouTube URLs:

- **Video**: `python orpheus.py https://www.youtube.com/watch?v=dQw4w9WgXcQ`
- **Short URL**: `python orpheus.py https://youtu.be/dQw4w9WgXcQ`
- **Playlist**: `python orpheus.py https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf`
- **Channel**: `python orpheus.py https://www.youtube.com/@ChannelName`

### Searching (CLI)

- **Video**: `python orpheus.py search youtube track "Never gonna give you up"`
- **Playlist**: `python orpheus.py search youtube playlist "80s hits"`
- **Channel**: `python orpheus.py search youtube artist "Rick Astley"`

**Note**: To search for a channel, use the `artist` search type.

### GUI Usage

1. Open OrpheusDL GUI
2. Go to the **YouTube** tab or select "YouTube" in the platform dropdown
3. Enter a URL or use the search function
4. Select results to download

## Cookies (Optional)

For age-restricted or premium content, you may need to provide cookies:

### Steps to get cookies:
1. Log in to YouTube in your browser.
   - **Important**: For age-restricted content, it is highly recommended to use a **Private/Incognito** window to ensure a fresh session.
2. Export cookies using a browser extension:
   - **Chrome/Edge**: [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - **Firefox**: [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)

   > **Tip for Incognito Mode**: Extensions are disabled by default in Incognito.
   > - **Chrome/Edge**: Right-click extension icon > Manage Extension > Toggle "Allow in Incognito".
   > - **Firefox**: Right-click extension icon > Manage Extension > "Run in Private Windows" > Allow.
   > - Refresh the YouTube page to see the extension icon.

3. Save the file as `youtube-cookies.txt` in the `config/` folder.
4. The module defaults to looking for `./config/youtube-cookies.txt`.

## Configuration

You can configure the module by editing `config/settings.json` (created after first run).

### Module Settings
Specific settings for the YouTube module:
- **`download_pause_seconds`**: Time to wait between downloads to avoid rate limiting. (Default: `5`)
- **`cookies_path`**: Path to your cookies file. (Default: `./config/youtube-cookies.txt`)
- **`download_mode`**: Set to `"sequential"` (default) for safer downloads, or `"concurrent"` for faster downloads.

## Audio Quality

YouTube does not provide lossless audio. The best available quality is:
- **Opus**: ~160 kbps (Best quality on YouTube)
- **AAC/M4A**: ~256 kbps
- **MP3**: Variable

The download format is determined by the global **Download Quality** setting in OrpheusDL:

| Global Setting | Format |
| :--- | :--- |
| **HiFi / Lossless** | **Opus** (Best available) |
| **High / Medium** | **AAC / M4A** |
| **Low / Minimum** | **MP3** |

> **Note**: You can override this per-download by right-clicking a search result and selecting a specific format.

## Troubleshooting

### "FFmpeg not found"
- Install FFmpeg from https://ffmpeg.org
- Set the path in `config/settings.json`

### "Video unavailable"
- The video may be region-locked
- Try using cookies for authentication
- The video may have been removed

### "Age-restricted content"
- Export and configure cookies from a logged-in YouTube session

### Rate Limiting
- YouTube may throttle downloads
- Wait a few minutes and try again
- Using cookies may help

## Notes

- Downloads are for personal use only - respect YouTube's Terms of Service
- Some content may be DRM-protected and unavailable
- Channel downloads are limited to recent uploads

## Credits

This module uses:
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - The actual download engine
- [OrpheusDL](https://github.com/bascurtiz/orpheusdl) - The main framework
