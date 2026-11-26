The application source and rebuild instructions are supplied in ../src and ../build.cmd. Dependencies are unmodified, pinned in requirements files. Their license texts are included in this directory.

Source distributions can be obtained with pip, for example:

    python -m pip download --no-binary=:all: --no-deps pystray==0.19.5

Upstream sources:

- pystray: https://github.com/moses-palmer/pystray (v0.19.5)
- Pillow: https://github.com/python-pillow/Pillow (12.3.0)
- six: https://github.com/benjaminp/six (1.17.0)
- comtypes: https://github.com/enthought/comtypes (1.4.17)
- psutil: https://github.com/giampaolo/psutil (7.2.2)
- PyInstaller and its bootloader: https://github.com/pyinstaller/pyinstaller (6.22.3)

To rebuild with a modified dependency, change its pinned requirement to your local source checkout and run build.cmd. Python/Tcl/Tk runtime license texts are also provided with the release.
