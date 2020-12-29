#!/usr/bin/env python3
import distutils.core  # setup
import glob            # glob
import os              # environ
import sys             # path
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import apkvitrine      # VERSION

def get_path(name):
    return Path("/" + os.environ[name].strip("/"))

sysconfdir = get_path("SYSCONFDIR")
webdir = get_path("WEBAPPDIR")

distutils.core.setup(
    name="apkvitrine",
    version=apkvitrine.VERSION,
    url="https://code.foxkit.us/sroracle/apkvitrine",
    author="Max Rees",
    author_email="maxcrees@me.com",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Environment :: Web Environment",
        "License :: OSI Approved :: University of Illinois/NCSA Open Source License",
        "Natural Language :: English",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Topic :: Database :: Front-Ends",
        "Topic :: Internet :: WWW/HTTP :: WSGI :: Application",
        "Topic :: System :: Software Distribution",
    ],
    license="NCSA",
    description="Package tracker and analyzer for APK repositories",
    long_description=Path("README.rst").read_text(),

    script_name="src/setup.py",
    packages=["apkvitrine"],

    package_data={"apkvitrine": [
        "data/*",
    ]},

    scripts=["webapp/apkvitrine.cgi"],
    data_files=[
        (
            str(sysconfdir),
            ["config.ini.in"],
        ),
        (
            str(webdir / "static"),
            glob.glob("webapp/static/*"),
        ),
    ],
)
