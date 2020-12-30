# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import collections  # defaultdict
import configparser # ConfigParser
import os           # environ
from pathlib import Path

VERSION = "0.2"
BUILDERS = "@builders"
DEFAULT = "@default"

_SYSCONFDIR = Path(__file__).parent.parent
SYSCONFDIR = Path(os.environ.get(
    "AV_CONFIG",
    _SYSCONFDIR,
)).resolve(strict=False)

def _config_map(s):
    d = {}
    for i in s.strip().splitlines():
        i = i.strip().split(maxsplit=1)
        d[i[0]] = i[1]
    return d

def _config_maplist(s):
    d = collections.defaultdict(list)
    for i in s.strip().splitlines():
        i = i.strip().split()
        d[i[0]].extend(i[1:])
    return d

def _ConfigParser(**kwargs):
    parser = configparser.ConfigParser(
        interpolation=None,
        comment_prefixes=(";",),
        delimiters=("=",),
        inline_comment_prefixes=None,
        empty_lines_in_values=True,
        converters={
            "list": lambda s: s.strip().splitlines(),
            "path": Path,
            "map": _config_map,
            "maplist": _config_maplist,
        },
        default_section=DEFAULT,
        **kwargs,
    )
    parser.BOOLEAN_STATES = {"true": True, "false": False}
    return parser

_DEFAULT_CONFIG = {
    "@default": {
        # distro (str)
        # repos (maplist)
        # index (str)
        "ignore": "", # list
        "startdirs": "", # map

        "bz.api": "", # str
        # bz.product (str)
        # bz.component (str)
        # bz.field (str)
        "bz.status": "", # list

        "gl.api": "", # str
        # gl.branch (str)

        # cgi.default_version (str)
        # cgi.data (str)
        "cgi.cache": "", # str

        "web.pagination": "25", # int
        "web.url.rev": "", # str
        "web.url.tree": "", # str
        "web.url.bug": "", # str
        "web.url.mr": "", # str
        "web.repology.link": "", # str
        "web.repology.badge": "", # str
    },
    # [@builders] gl.token (str)
    # [@builders] gl.api (str - override)
}

def config(version=None):
    files = sorted(SYSCONFDIR.glob("*.ini"))

    config = _ConfigParser()
    config.read_dict(_DEFAULT_CONFIG)
    config.read(files)

    if version:
        return config[version]

    return config
