#!/usr/bin/env python3
# SPDX-License-Identifier: NCSA
# Copyright (c) 2018-2020 Max Rees
# See LICENSE for more information.
import ctypes    # c_char_p, CDLL, c_int, c_long, Structure
import enum      # IntFlag
import functools # cmp_to_key

class APK_VER(enum.IntFlag):
    UNKNOWN = 0
    EQUAL = 1
    LESS = 2
    GREATER = 4
    FUZZY = 8

APK_OPS = {
    ">=": APK_VER.GREATER | APK_VER.EQUAL,
    "<=": APK_VER.LESS | APK_VER.EQUAL,
    ">~": APK_VER.GREATER | APK_VER.EQUAL,
    "<~": APK_VER.LESS | APK_VER.EQUAL,
    ">": APK_VER.GREATER,
    "<": APK_VER.LESS,
    "=": APK_VER.EQUAL,
    "~": APK_VER.EQUAL,
}

class _apk_blob_t(ctypes.Structure): # pylint: disable=too-few-public-methods
    _fields_ = [
        ("len", ctypes.c_long),
        ("ptr", ctypes.c_char_p),
    ]

    def __init__(self, s): # pylint: disable=super-init-not-called
        s = s.encode("utf-8")
        self.len = len(s)
        self.ptr = ctypes.c_char_p(s)

def vercmp(a, b, fuzzy=False):
    a = _apk_blob_t(a)
    b = _apk_blob_t(b)
    fuzzy = 1 if fuzzy else 0
    return APK_VER(_LIBAPK.apk_version_compare_blob_fuzzy(a, b, fuzzy))

@functools.cmp_to_key
def verkey(a, b):
    r = vercmp(a, b)

    if r == APK_VER.LESS:
        return -1
    if r == APK_VER.EQUAL:
        return 0
    if r == APK_VER.GREATER:
        return 1

    return None

def ver_is(a, op, b):
    if op not in APK_OPS:
        raise ValueError("Invalid op " + repr(op))

    fuzzy = "~" in op
    return vercmp(a, b, fuzzy) & APK_OPS[op]

def is_older(old, new):
    return vercmp(old, new) == APK_VER.LESS

def is_same(old, new):
    return vercmp(old, new) == APK_VER.EQUAL

_LIBAPK = ctypes.CDLL("libapk.so")
_LIBAPK.apk_version_compare_blob_fuzzy.argtypes = [
    _apk_blob_t, _apk_blob_t, ctypes.c_int,
]
_LIBAPK.apk_version_compare_blob_fuzzy.restype = ctypes.c_int
