# NF3D
I wanted to take something that sounds simple (creating stereoscopic subtitles for side by side 3D movie files) and actually make it simple.
NF3D is a gui that will open an sbs mkv, load any subtitles file (internal or external) and create 3D SBS subtitles with per title depth placement. Subtitle appearance is completely customisable, per title edits can be made. Outputs either an .ass file or muxes a copy into the mkv along with font files to ensure compatibility.
Requires mkvtoolnix (to open and write tracks to mkv), subtitle edit (for the OCR for optical subs), ffmpeg and python scripts to analyse depth etc. The installer will check if dependencies are already installed and can install any that are outstanding.
