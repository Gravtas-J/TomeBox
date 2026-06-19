# Third-Party Licenses

TomeBox depends on and bundles the open-source components listed below. Each
remains under its own license; copies of those licenses are included with the
binary distribution where required.

TomeBox's own source code is released under the **MIT License** (see `LICENSE`).
However, the bundled distribution includes copyleft components — most
significantly **`audible` (AGPL-3.0)** and **`mutagen` (GPL-2.0-or-later)** — so
the combined binary release of TomeBox is, in practical effect, governed by the
**GNU Affero General Public License v3.0** (see `LICENSE.AGPL`). The complete
corresponding source is available at <https://github.com/Gravtas-J/tomebox>.

## Copyleft components (these drive the distribution's license)

| Package | Version | License | Source |
|---|---|---|---|
| audible | 0.10.0 | AGPL-3.0-only | <https://github.com/mkb79/Audible> |
| mutagen | latest | GPL-2.0-or-later | <https://github.com/quodlibet/mutagen> |
| pystray | 0.19.5 | LGPL-3.0-only | <https://github.com/moses-palmer/pystray> |

## Permissive components

| Package | Version | License | Source |
|---|---|---|---|
| Pillow | 12.2.0 | MIT-CMU (HPND) | <https://python-pillow.github.io> |
| tkinterdnd2 | 0.4.3 | MIT | <https://github.com/Eliav2/tkinterdnd2> |
| requests | 2.34.2 | Apache-2.0 | <https://github.com/psf/requests> |
| rsa | 4.9 | Apache-2.0 | <https://stuvel.eu/rsa> |
| fastapi | 0.136.1 | MIT | <https://github.com/fastapi/fastapi> |
| uvicorn | 0.47.0 | BSD-3-Clause | <https://www.uvicorn.org> |
| httpx | 0.28.1 | BSD-3-Clause | <https://github.com/encode/httpx> |
| wakepy | 1.0.0 | MIT | <https://github.com/wakepy/wakepy> |
| pycaw *(Windows)* | 20251023 | MIT | <https://github.com/AndreMiras/pycaw> |
| qrcode | 8.2 | BSD-3-Clause | <https://github.com/lincolnloop/python-qrcode> |
| rapidfuzz | 3.14.3 | MIT | <https://github.com/rapidfuzz/RapidFuzz> |
| sounddevice | 0.5.5 | MIT | <https://github.com/spatialaudio/python-sounddevice> |

## Bundled binaries

| Component | License | Source |
|---|---|---|
| FFmpeg | LGPL-2.1+ **or** GPL-2.0+ (build-dependent) | <https://ffmpeg.org> |

FFmpeg is invoked as a separate process (aggregation), so it does not on its own
pull your code under the GPL the way an imported library does — but you must
still ship its license and confirm which build you bundle. A GPL FFmpeg build
adds GPL-2.0 obligations; an LGPL build is lighter.

## Note on the AGPL network-use clause

`audible` is AGPL-3.0. Because TomeBox includes a web companion server
(FastAPI/uvicorn) that users interact with over a network, AGPL-3.0 Section 13
can require that those network users be offered the corresponding source.
TomeBox's source is public, which satisfies this; keep the repository link
reachable from the app/companion so remote users can find it.

---

*Licenses verified from each package's published PyPI metadata and repository.
This summary is provided in good faith and is not legal advice.*