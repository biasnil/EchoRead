# EchoRead

An offline Speechify-style reader. Drop in a PDF, an image, some text (or, if you switch it on, a web page) and EchoRead reads it aloud in natural Kokoro voices. Click any paragraph to jump there, change speed instantly, bookmark, resume where you stopped, and export the text or audio.

After the first-run model downloads, everything runs on your computer.

## Install

Python 3.10 – 3.12 is recommended. A virtual environment is a good idea.

**CPU (works everywhere):**
```
pip install -r requirements-cpu.txt      # requirements.txt is the same
```

**NVIDIA GPU (Windows, PowerShell, venv activated):**
```
.\install_gpu.ps1
```
That script installs the shared packages, a CUDA 13 build of `onnxruntime-gpu` and `paddlepaddle-gpu` for CUDA 13 (both come from their own servers, which a requirements file can't express), then runs `python gpu_check.py`. It needs an NVIDIA driver, the CUDA 13 toolkit and cuDNN 9 for CUDA 13 first (cuDNN: NVIDIA's installer, or `pip install nvidia-cudnn-cu13`). EchoRead finds the CUDA/cuDNN libraries itself. Then set *Settings → Device → GPU* and restart.

The CPU and GPU builds of ONNX Runtime and PaddlePaddle share the same module names, so **one environment can hold only one flavour** (and leftovers like `onnxruntime-directml` shadow them). To switch, uninstall the other one first, or keep two virtual environments. `python gpu_check.py` tells you what is found, what is missing, and whether the GPU really runs.

There are no torch or transformers pins: voice cloning was removed, so nothing needs them.

## Run

```
python main.py
```

The first run downloads the speech model (Kokoro) and, when you first OCR something, the OCR model. Progress appears in the status bar.

## Build a Windows .exe (PyInstaller)

Two ready-made builds, from one spec (`echoread.spec`):

```
build_CPU.bat     ->  dist\EchoRead-CPU\EchoRead-CPU.exe     (works on any PC)
build_GPU.bat     ->  dist\EchoRead-GPU\EchoRead-GPU.exe     (NVIDIA GPU, CUDA 13)
```

Run one from the EchoRead folder (`.\build_CPU.bat`). It creates its own virtual environment (`.build-cpu` / `.build-gpu`), installs the right packages (the GPU one runs `install_gpu.ps1`), checks the environment really is that flavour, builds, and then **runs the new exe's self-test**. Options: `--clean` (fresh build; use it after changing the spec or requirements), `--debug` (keeps a console window so you can see output), `--current` (use the virtual environment you already activated).

- **Python 3.12** is recommended. The first build downloads several GB; the finished folders are large (the GPU one is several GB because it carries the NVIDIA libraries). Copy the whole `dist\EchoRead-…` folder to move the app.
- **CPU and GPU can't share one environment** (their onnxruntime/paddle builds share module names), which is why there are two virtual environments and why the build stops if the environment isn't cleanly one flavour.
- **The speech and OCR models are not bundled.** They download on first use into their usual caches, as when running from source.
- **GPU build:** it still needs an up-to-date NVIDIA driver. It bundles the NVIDIA libraries that pip installed (cuDNN, cuBLAS, CUDA runtime); if the build environment had none, it relies on the CUDA 13 toolkit and cuDNN 9 installed on the PC it runs on.
- **Self-test:** `EchoRead-CPU.exe --selftest` writes `selftest_report.txt` beside the exe and lists what's OK or FAIL: bundled package metadata, native libraries (eSpeak, libsndfile, Paddle, ONNX Runtime), data files, hidden imports, and whether the build matches its flavour. The build scripts fail if it doesn't pass.
- **Logs:** see *Error reports* below. A windowed exe has no console, so library output that would have gone to it is saved in `%APPDATA%\EchoRead\logs\console.log`; a crash before the window is up shows a message box pointing at that folder.

**If the exe fails but `python main.py` works**, something the build didn't copy is missing. Suspect, in order: package metadata (`importlib.metadata` checks; PaddleX's "requires additional dependencies" error), native libraries that a library finds by file path, data files, lazily imported modules. `--selftest` and `--debug` show which one.

## Use

1. **Drop a file** anywhere on the window (or press `Ctrl+O`, or use **＋ Add**). PDF, **EPUB**, **Word (.docx)**, PNG/JPG/TIFF/BMP/WebP, and .txt/.md all work. EPUB chapters and Word headings are kept as paragraphs of their own, footnotes and the contents page are left out, and tables are read row by row. A copy-protected (DRM) EPUB can't be opened. Old Word files (.doc) can't be opened: save them as .docx first.
2. **PDFs with real text** are read straight from their text layer, with the layout understood (see *Reading PDFs with real text*). Scans and images go through PaddleOCR. (Settings → *Default OCR engine* can force OCR.)
3. **Regions** (menu in the reader, or *Study → Pick regions first* on Home): drag boxes on the page.
   - **Keep** / **Ignore** boxes; unmarked areas are kept.
   - **Only** reads just your Keep boxes, in the order you drew them.
   - **Apply to all pages** repeats the box on every page, handy for headers and footers.
   - Press **Run OCR** to extract again.
4. **Click a word** to play from that word (click the first word, or anywhere else in the paragraph, to start at its beginning). The paragraph, the sentence and the exact word being spoken are highlighted as it plays, and it moves on automatically.
5. **Drag across text** to highlight it in a colour (see *Highlighting*). Right-click a paragraph for *Highlight paragraph*, *Skip* (not read, still shown) and *Ignore* (removed from reading and export).

## Exporting

**Share → Export…** (or **Export…** on a Library card, which opens the document first) shows a small two-step window:

1. **Text or Audio?**
2. **How?**
   - *Text:* a single text file (.txt, .md or .json), or each chapter as its own text file (.txt or .md).
   - *Audio:* **a single audio file with every chapter joined into one** (the whole book, in order, with a pause between chapters), **each chapter as its own audio file** (numbered, in a folder; chapters already exported are skipped, so a stopped export carries on when you run it again), or **only the chapter you are on**. .mp3 is smaller, .wav is best quality.

"Each chapter" is only available for documents that were split into chapters (long books). Pictures, skipped paragraphs and ignored paragraphs are left out. Audio you already listened to is reused; the rest is made now, so a very long book takes about as long as reading it aloud unless most of it was cached. There are no chapter markers inside the single file. *Share → Export per-paragraph audio folder…* is still there.

## First-run setup

On the very first launch EchoRead shows **Welcome to EchoRead**: a name (required to continue) and an avatar colour (six swatches). **Skip for now** closes it without creating a profile; the bottom of the sidebar then shows a **Set up profile** button, and the dialog never pops up again by itself. Once you have a profile the sidebar shows your coloured avatar (the first letter of your name) and your name. You can also add a **profile picture**: drop any picture (PNG, JPG, GIF, WebP, BMP, TIFF…) on the round preview or click it to browse. It is cropped to a centred square and shown as a circle instead of the initial; the colour is used again if you remove it. Change the name, colour or picture later in *Settings → Edit Profile*. Emptying the name there returns you to *Set up profile*.

## Profile & privacy

The profile is a name, a colour and optionally a picture, saved in `%APPDATA%\EchoRead\settings.json` (`profile.name`, `profile.avatar_color`, `profile.avatar_image`, `profile.onboarded`). The picture is a small square PNG copy kept in `%APPDATA%\EchoRead\profile\`; your original file is never changed or moved. It is used only to label the sidebar, and it is never sent anywhere. Older installs that had a hard-coded name have it removed on first start.

## Highlighting

- **While reading:** the paragraph being read is tinted, the **sentence** gets a soft accent background and the **word** a stronger one, following the audio about 30 times a second. Turn the sentence and word highlights on or off in *Settings → Highlight Sentence / Highlight Word*. If the speech model reports where each word is, that is used; otherwise the position is estimated from word lengths and punctuation, so it can drift by a fraction of a second on long sentences.
- **Your own highlights:** press and drag with the left mouse button across the characters you want (a drag of about 6 px or more; a shorter movement is just a click). When you let go a small palette opens with five colours (yellow, green, pink, purple, blue) and *Copy*. Pick a colour to keep the highlight, or click elsewhere to cancel. A selection stays inside one paragraph.
- **Change or remove:** right-click a highlight for *Change color*, *Remove highlight* or *Copy*. Right-click a paragraph for *Highlight paragraph* / *Remove highlights*.
- **Turn dragging off/on:** `Ctrl+Shift+H`. While it is off, dragging across text does nothing special.
- **Where they are stored:** `%APPDATA%\EchoRead\highlights\<document id>.json`, one file per document (paragraph number, character range, colour). They come back every time you open the document. If the document's text changes (for example after re-running OCR) its highlights are dropped, like bookmarks. Whole-paragraph highlights from older versions are converted once.

## Fonts, size and spacing

*Settings → Reading font* (or `Ctrl+F`) lists the reading fonts: Courier (Courier New), Inter, Georgia (default), Lora, OpenDyslexic, Source Code Pro, Source Sans 3 and Times New Roman. Pick one and it is applied to the open document immediately, together with the **Text size** (12–28 px) and **Line spacing** (1.2–2.0) sliders, with a live preview. *Cancel* puts the previous look back. Georgia, Times New Roman and Courier New come with Windows; Inter, Lora, Source Code Pro and Source Sans 3 are bundled in `assets/fonts` (SIL Open Font License). OpenDyslexic appears once its font file is added to `assets/fonts` (see the README there).

Reading layout: body line height defaults to 1.65x, paragraphs are about 1.3 em apart, the text column is at most about 720 px wide with 56 px margins, cards use 24 px padding and Home sections sit 32 px apart (all in `SPACING` in `ui/theme.py`).

## Volume and playback icons

The Listen pill has a **volume slider** (0–100, default 100) with a mute button; it changes the sound at once without restarting playback, and is remembered (`playback.volume`). Keys: `+` / `-` (and `=`) change it by 5. The buttons use line-style SVG icons from `assets/icons` (`play`, `pause`, `resume`, `previous`, `next`, `stop`, `bookmark`, `bookmark_filled`, `volume`, `volume_muted`): white on the dark theme, near-black on the light theme, accent-coloured on hover. To restyle one, replace the file, keeping `stroke="currentColor"` so the theme colour is applied.

## Error reports

- **Log file:** `%APPDATA%\EchoRead\logs\echoread.log`, rotating (5 MB, 3 files). Every line has the time, level, module and, for errors, the full traceback.
- **Unexpected errors** show *"Something went wrong while processing this file."* with an expandable **Show details** area, **Copy traceback** and **Open log file**.
- **Known situations** get their own message: an unsupported file type is a short pop-up message; a PDF that won't open offers an OCR fallback; OCR that finds nothing says *"No text detected. Try adjusting Keep/Ignore regions."*; a page that can't be fetched says *"Couldn't read this page. Check the URL or disable Internet Usage."*; with no sound output EchoRead plays silently (highlighting keeps running) and warns you; if the disk is full while caching audio, old cached audio is deleted and the write retried; the first-run speech-model download shows a progress bar in the status bar.

## Reading PDFs with real text (columns, headings, footnotes, Bibles)

EchoRead reads a PDF's text with its layout: it knows the size, font and position of every piece of text, so it can do what a person does.

- **Columns** are read one after the other (left column top to bottom, then the right one), with a full-width heading read first. A sentence that carries over a column or page break stays one paragraph.
- **Headings** (book and chapter titles, section headings such as "THE CREATION") are kept as paragraphs of their own.
- **Page headers, footers, page numbers and printer's marks** are left out.
- **Footnotes** at the foot of a page are left out; the small raised letters or numbers that point to them are never read.
- **Verse numbers (Bibles):** when a PDF looks like a Bible (verse numbers going 1, 2, 3 on several pages), each verse becomes its own paragraph and the numbers are not read, so you can click, bookmark and jump by verse. A big chapter number becomes its own short paragraph "Chapter 3" (placed before the section heading, so it works with the chapter list). Introductions and other pages without verses are read as normal text.
- **Ordinary text:** paragraphs are found from indentation, gaps and short last lines; line-break hyphens are removed ("in-formation" becomes "information") but real compounds keep theirs; bullets become separate paragraphs and the bullet dot is not read; table-of-contents dot leaders are dropped.

**Tables.** A table with ruled lines is read row by row, using the first row as column names: "Name: Ana. Age: 30. City: Oslo." A table without a header row is read cell by cell. Tables without drawn lines are not detected. *Settings → Read tables row by row* turns it off. (Finding tables costs time, so it only runs on pages that have several long ruled lines.)

**Formulas.** *Settings → Say formulas out loud* (on) writes formulas the way they are said, in the text itself, when a file is opened or re-extracted, and also for pasted text: `x² + y² = z²` becomes "x squared plus y squared equals z squared", `E = mc²` "E equals mc squared", `√16` "the square root of 16", `≤` "is less than or equal to", `15%` "15 percent", Greek letters by name. It is careful: an operator only becomes a word next to numbers or letters, so ordinary sentences, dates, ranges, hyphenated words and web addresses are left alone. It cannot read stacked fractions, integrals or big equations, because a PDF stores those as loose pieces.

**Pictures stay in the document.** Charts, photos and diagrams (also diagrams drawn with shapes and labels) are kept and shown in the reader between the paragraphs, in the place where they are on the page. They are never read aloud, the words written inside them ("FDD", "Download") are not read as text, and they are left out of word counts and exports. The reader plays on to the next paragraph when you click one. This works for PDFs with text, for scanned pages and images (found where a page has a big area that isn't text), and with Smart layout. *Settings → Show pictures in the reader* (on) turns it off. *Settings → Read the words inside pictures* (off) reads a figure's labels, or OCRs a picture that has no text of its own. Pictures are stored in `%APPDATA%\EchoRead\images\`. A file already opened keeps its old text until you use Regions → Re-run text extraction.

**Smart layout for scans (off by default).** *Settings → Smart layout for scans* uses PaddleOCR's PP-StructureV3 on scanned pages: it finds the page's regions, reads them in the right order (columns), and tells titles, footnotes, headers, footers and tables apart, and those regions follow your *headings / footnotes / headers / tables* choices above. It is slower and heavier and downloads extra models the first time you use it; if anything goes wrong EchoRead says so in the status bar and carries on with the standard OCR. Verse numbers in scans are still not removed.

**Settings → Reading PDFs and scans:** *Read headings* (on), *Read footnotes* (off), *Read page headers, footers and page numbers* (off), *Verse numbers* (Automatic / Always / Never) and *OCR sharpness*. They apply to files you open from now on, or after **Regions → Re-run text extraction** (a file that was already extracted keeps its old text until then). Keep/Ignore regions keep working on top of all this.

**Scans (OCR):** columns in a scan are also read one after the other. *OCR sharpness → Sharp* renders each page larger and lets the detector keep that resolution, which helps small print but is slower and uses more memory. Verse numbers and footnote marks inside scanned text are not detected (OCR gives lines, not fonts); scanned Bibles will still contain them.

## Speed precaching (0.75x – 2.0x)

When a document (or, for a long one, the open chapter) opens, a background worker prepares every paragraph at the speed you are listening at, plus any other speeds you chose in *Settings → Precache speeds*, so that switching to those speeds is instant. **By default only 1.0x is chosen.** A speed you didn't choose is made the first time you switch to it (a short wait), and from then on it is prepared too.

- Order: the current paragraph and the next two at your speed, the same paragraphs at the other speeds, then the rest of the document forward, then wrapping around. If you jump somewhere, the plan re-prioritizes around the new spot. If you press play on something not cached yet, it is synthesized first.
- Opening a different document cancels the old work.
- Cache location: `%APPDATA%\EchoRead\cache\<chapter hash>\<speed>\<paragraph>.flac`. Changing the voice starts a fresh cache for that voice.
- **Size limit:** *Settings → Keep the audio cache under…* (default 5 GB). Past that, the chapters you read longest ago are deleted first; the one you have open never is.
- Cached audio is lossless FLAC. It is real disk space, so **Settings → Audio cache** shows the size, and *Clear cache* empties it (the open document keeps working and is re-cached next time you open it).
- Each speed you tick adds one more full copy of the background work and of the disk space. Ticking four or more shows a warning (it can slow EchoRead down while a long document is prepared and fills the cache much faster); one or two is usually enough. Ticking none is allowed: only the speed you are listening at is prepared. Changes take effect when you press Save. Older settings files (which had a single "precache every speed" switch) start from the new default of 1.0x.

## Internet Usage (off by default)

**What it does:** with *Settings → Internet Usage* on, the **Paste Link** card and menu items work. EchoRead fetches the page you pasted, extracts the article text (readability), and reads it. It uses a normal desktop browser User-Agent and checks the site's `robots.txt`; if the site asks automated tools not to fetch that page, EchoRead won't.

**What it doesn't do:** no telemetry, no trackers, no analytics. The only requests are to the site you pasted (its `robots.txt` and the page itself).

**Being straight about it:** the speech and OCR models download once on first use regardless of this switch. After that you can run fully offline.

When the switch is off, Paste Link is greyed out with the tooltip "Enable Internet Usage in Settings."

## Very long documents (a whole Bible, a long novel)

Documents of 300+ paragraphs are split into **chapters** automatically, and the reader only ever holds the open chapter (at most 250 paragraphs). That keeps the window fast, keeps precaching bounded, and lets you carry on where you left off in a 30,000-paragraph book.

- **How chapters are found:** headings like `Chapter 12`, `Part II`, `Psalm 23`, `Prologue`, Chinese `第12章` / Korean `제12장`, and Bible book names (`Genesis`, `1 Corinthians`, or `Genesis 1`). A chapter over 250 paragraphs is cut into parts. If no headings are found, the text is cut into parts of about 250 paragraphs.
- **Moving around:** the **☰ Chapters** panel, the *Previous / Next chapter* buttons under the text, or `PgUp` / `PgDn`. Reading carries on into the next chapter by itself. The next chapter's first line is synthesized when you get there, so expect a brief pause at chapter boundaries.
- **Bookmarks** work across the whole book. The panel lists them all, labelled with their chapter, and clicking one takes you there.
- **Scans:** OCR saves each finished page as it goes. If you cancel, close the app or it crashes, opening the file again carries on from the last finished page. A page that can't be read is skipped and reported (the rest is kept). *Regions → Edit regions → Run OCR* then retries only those pages. Changing regions or OCR settings discards saved pages so old and new results never mix.
- **Export:** *Share → Export audio* saves the open chapter. *Share → Export every chapter as…* writes one numbered file per chapter into a folder and can be run again to finish what a stopped run left, skipping chapters that are already done. Audio is written to disk as it is generated, so memory use stays flat. Text export always covers the whole document.

## Voices

- **Presets:** Kokoro voices in several languages (English US/UK, Spanish, French, Italian, Portuguese; Japanese and Chinese need extra PyKokoro packages).
- **Voice packs:** *Voices → Import voice pack…* accepts
  - `.npy` / `.npz` / `.bin` Kokoro voice style arrays (shape 511×1×256), or
  - `.json` blend recipes, e.g. `{"blend": "af_heart:60,am_adam:40", "lang": "en-us"}`.
- There is no voice cloning.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `←` `→` or `↑` `↓` | Previous / next paragraph |
| `[` `]` | Speed down / up |
| `+` `-` | Volume up / down |
| `B` | Bookmark the current paragraph |
| `Ctrl+Shift+H` | Turn drag-to-highlight on / off |
| `Enter` | Play from the current paragraph |
| `PgUp` `PgDn` | Previous / next chapter |
| `Ctrl+O` | Open a file |
| `Ctrl+,` | Settings |
| `Ctrl+F` | Reading font (opens Settings at the font list) |
| `?` | Shortcut help |

Reader shortcuts only act while the reader has focus, so typing in text boxes is never hijacked.

## Bookmarks, export and library

- **Bookmark:** click a paragraph's **left edge** (or press `B`). The *Bookmarks* panel lists them; click one to jump.
- **Resume:** each document remembers its last paragraph and speed, plus bookmarks, in `%APPDATA%\EchoRead\library.json` (coloured highlights are in `highlights\`). Reopening picks up exactly there. If you re-extract and the text changes, that document's saved position and bookmarks reset.
- **Library** (sidebar) lists everything you've read; the sidebar Tasks/Files tabs show recents.
- **Share menu:**
  - Export text as `.txt`, `.md` or `.json`.
  - Export audio as `.wav` or `.mp3` (no ffmpeg needed) at the current speed and voice.
  - Export a per-paragraph audio folder (`0001.wav`, … plus `index.json`).
  - Audio exports leave out skipped and ignored paragraphs.
- **Theme:** dark by default; *Settings → Theme* or *View → Switch theme*.

## Troubleshooting

- **A Japanese or Chinese voice says "needs the … speech add-on":** those two languages need extra packages that English doesn't. `requirements-base.txt` now lists them (Chinese: `kokorog2p[zh]`; Japanese on Windows: `pyopenjtalk-plus`, a prebuilt package that carries its own dictionary, about 100 MB; kokorog2p's own `[ja]` extra would compile a C++ library and need the Visual C++ Build Tools). If your environment was made before that, install them in EchoRead's virtual environment with `pip install "kokorog2p[zh]" pyopenjtalk-plus` and restart. An .exe build only includes them if they were installed when it was built; `--selftest` shows a "Chinese / Japanese voice add-ons" line.
- **"eSpeak … could not be initialized":** `pip install espeakng-loader`, then restart.
- **`transformers<5.0` / torch errors:** EchoRead doesn't use them. The conflict came from voice cloning, which is removed. If pip complains, you're probably reusing an old environment with chatterbox in it. Make a fresh virtual environment.
- **`ConvertPirAttribute2RuntimeAttribute not support` during OCR:** a Paddle 3.x CPU (oneDNN) bug that hits some pages. EchoRead now retries automatically in compatibility mode; nothing to do. The other console noise (IPA text, ccache warning, "falling back to wrap mode") is harmless.
- **GPU:** run `python gpu_check.py`. Speech needs the CUDA 13 build of `onnxruntime-gpu` plus CUDA 13 and cuDNN 9 libraries; OCR needs `paddlepaddle-gpu`. If the GPU can't start for speech, EchoRead switches to the CPU and says why in the status bar. Device changes apply after a restart.
- **Cache too big / weird audio after an update:** Settings → lower *Keep the audio cache under…* or *Clear cache*, or delete `%APPDATA%\EchoRead\cache`.
- **Chapters not detected in a long document:** it falls back to parts of about 250 paragraphs. If the file has no recognisable headings that's expected; the *Chapters* list will show "Part 1 · paragraphs 1–249" and so on.
- **No sound:** check the Windows default output device; EchoRead plays through it. If none can be opened EchoRead warns you and keeps going silently (the highlight still moves) so nothing gets stuck. Also check the volume slider isn't at 0.
- **Reset everything:** delete `%APPDATA%\EchoRead`.

## Credits

[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M) via [PyKokoro](https://pypi.org/project/pykokoro/), [PyMuPDF](https://pymupdf.readthedocs.io/), [PyQt6](https://www.riverbankcomputing.com/software/pyqt/), [readability-lxml](https://github.com/buriy/python-readability), [soundfile](https://python-soundfile.readthedocs.io/), [sounddevice](https://python-sounddevice.readthedocs.io/) and [pypdfium2](https://github.com/pypdfium2-team/pypdfium2). Fonts: Inter, Lora, Source Code Pro and Source Sans 3 under the SIL Open Font License.
