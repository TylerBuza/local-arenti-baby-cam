# Third-party notices

Project-authored code is MIT. Third-party components keep their own licenses.
No Arenti SDK, vendor account client, firmware, private home credential or camera
recording is distributed. The icon is drawn from project source.

## Windows installer and source-based setup

The Windows EXE contains an Inno Setup wizard and allowlisted project source,
**not Python, Qt or FFmpeg binaries**. The wizard downloads the Python project's
official NuGet x64 runtime (3.13.17, archive SHA256
`dce409744b088f94c6e37ba41f8100c384c27e029e843237364c602d30d475e5`)
and verifies the archive before executing it. The alternative source ZIP requires
Python installed by the user. Both installers download official
PyPI wheels; package metadata and included license texts remain in the installed
runtime. The application imposes no restriction on replacing shared libraries or
debugging modifications to LGPL components.

- Python: PSF license — https://www.python.org/psf/license/
- Inno Setup6.7.4: Copyright (C) 1997-2026 Jordan Russell; portions Copyright
  (C) 2000-2026 Martijn Laan. Unmodified installer engine, retaining its upstream
  copyright notices and addresses. License: https://github.com/jrsoftware/issrc/blob/is-6_7_4/license.txt
- PySide6/Shiboken/Qt: LGPLv3 or applicable upstream terms — https://www.qt.io/licensing/open-source-lgpl-obligations
- PyAV: BSD-3-Clause — https://github.com/PyAV-Org/PyAV
- FFmpeg used by PyAV/Qt: LGPL/GPL depending on upstream build — https://ffmpeg.org/legal.html
- Requests: Apache-2.0; urllib3: MIT; certifi: MPL-2.0; charset-normalizer: MIT; idna: BSD-3-Clause.
- pywin32: PSF-based license; psutil: BSD-3-Clause.
- cryptography: Apache-2.0 OR BSD-3-Clause; cffi: MIT; pycparser: BSD-3-Clause.
- qrcode: BSD-3-Clause; colorama: BSD-3-Clause.

`requirements.txt` pins direct application dependencies. Pip resolves transitive
dependencies; exact installed versions are available through `pip freeze` and
package metadata. Building a bundled Windows executable is a separate redistribution
task: inspect exact FFmpeg/Qt build licensing and supply corresponding source where
required before sharing such a bundle.

## Android APK

- AndroidX AppCompat/Core/Media3 and Android support components: Apache-2.0.
- ZXing Android Embedded and ZXing Core: Apache-2.0.
- Bouncy Castle bcpkix/bcprov/bcutil: MIT-style Bouncy Castle license.
- Kotlin components: Apache-2.0; JSpecify: Apache-2.0 where included.

Full Apache-2.0 and Bouncy Castle notices are included in the APK's
`assets/LICENSES.txt`, with packaged META-INF notices retained when supplied.
Sources: https://github.com/androidx/media · https://github.com/journeyapps/zxing-android-embedded ·
https://github.com/zxing/zxing · https://github.com/bcgit/bc-java

Gradle wrapper: Apache-2.0, https://github.com/gradle/gradle . Build/test-only
JUnit and Android test libraries are not included in the public release APK.
