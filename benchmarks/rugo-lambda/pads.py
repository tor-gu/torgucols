"""
Build the package-size sweep: the baseline handler plus a file of random bytes.

Usage: python pads.py

Writes pkg/pad<N>/ and dist/pad<N>.zip for each size in PAD_MB. Random bytes
don't compress, so each zip is about N MB (zipped ≈ unpacked). Seeded, so a
rebuild gives the same bytes.
"""

import os
import random
import shutil
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PAD_MB = [5, 10, 20, 30, 40]  # direct upload allows up to 50 MB zipped


def main():
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    for mb in PAD_MB:
        pkg = os.path.join(HERE, "pkg", f"pad{mb}")
        shutil.rmtree(pkg, ignore_errors=True)
        os.makedirs(pkg)
        shutil.copy(os.path.join(HERE, "handlers", "baseline.py"), os.path.join(pkg, "handler.py"))
        with open(os.path.join(pkg, "filler.bin"), "wb") as f:
            f.write(random.Random(mb).randbytes(mb * 2**20))
        dest = os.path.join(HERE, "dist", f"pad{mb}.zip")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("handler.py", "filler.bin"):
                z.write(os.path.join(pkg, name), name)
        print(f"pad{mb:<3} zipped {os.path.getsize(dest) / 2**20:6.2f} MB")


if __name__ == "__main__":
    main()
