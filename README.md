# Transcriptrick

Transcripts and AI summaries from YouTube, Spotify and SoundCloud links, plus MP4 video and MP3 audio downloads of videos and whole playlists from YouTube and most other video sites.

## Install

Download **Transcriptrick-Setup.exe** from the latest release and double-click it:
https://github.com/rickbesada-netizen/-transcriptrick/releases/latest/download/Transcriptrick-Setup.exe

It installs for your Windows user only, so no admin password is needed, and adds Transcriptrick to the Start menu. Python is not needed. If Windows says it protected your PC, choose **More info**, then **Run anyway**: the app isn't code-signed, which is what triggers that warning.

Transcriptrick opens in its own window. Close the window to quit.

## Using it

- **Transcribe**: paste a link and press Transcribe. Playlists let you tick which items to include.
- **Download**: paste a video or playlist link and press Download, then choose MP4 video (with quality) or MP3 audio. Files go to `Downloads\Transcriptrick`, with a folder per playlist. Change the folder in Settings.
- **Summaries**: add a free Groq key in Settings (https://console.groq.com/keys). The same key makes transcription much faster.

Spotify's audio is copy-protected, so Spotify links can be transcribed (from lyrics or the podcast's public feed) but not downloaded.

The downloader updates itself in the background, and the update takes effect the next time Transcriptrick starts.

## Your data

Transcripts, settings and keys are stored in `%LOCALAPPDATA%\Transcriptrick`. Uninstalling keeps them, so reinstalling picks up where you left off.

## Developers

Run from source with `START.bat` (Windows) or `./start.sh`. Tests: `python -m pytest tests`. Each push to main builds the installer on GitHub Actions and publishes it as a release named after the version in `app/__init__.py`.
