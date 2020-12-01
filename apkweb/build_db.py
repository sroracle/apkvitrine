#!/usr/bin/env python3
# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import argparse       # ArgumentParser
import collections    # defaultdict
import datetime       # datetime
import json           # load
import logging
import sqlite3        # connect
import urllib.parse   # urlencode
import urllib.request # urlopen
from pathlib import Path

from apkkit.base.index import Index

import apkweb
import apkweb.models
import apkweb.version

GL_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
BZ_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

def init_db(name):
    try:
        Path(name).unlink()
    except FileNotFoundError:
        pass
    schema = Path("schema.sql").read_text().strip()
    db = sqlite3.connect(str(name))
    db.executescript(schema)
    return db

def atomize(spec):
    if not any(i in spec for i in apkweb.version.APK_OPS):
        return spec

    for op in apkweb.version.APK_OPS:
        try:
            name, ver = spec.split(op, maxsplit=1)
        except ValueError:
            continue

        return name

    # Not reached
    assert False

def pkg_newest(pkgs, new, *, name=None):
    name = new.name if name is None else name

    old = pkgs.get(name)
    if not old:
        pass
    elif apkweb.version.is_older(old.version, new.version):
        pass
    elif int(old._builddate) < int(new._builddate):
        pass
    else:
        new = old
    pkgs[name] = new

def prov_newest(provs, new):
    pkg_newest(provs, new)
    for name in new.provides:
        # apkkit doesn't currently support provider_priority...
        pkg_newest(provs, new, name=atomize(name))

def pull_indices(conf, version):
    all_pkgs = {}
    pkgs = collections.defaultdict(dict)
    provs = {}

    all_arch = set()
    repos = conf.getmaplist("repos")
    ignore = conf.getlist("ignore")
    for repo, arches in repos.items():
        for arch in arches:
            logging.info("Parsing %s/%s...", repo, arch)
            all_arch.add(arch)

            # Noisy
            logging.disable(logging.INFO)
            index = Index(
                url=conf["url"].format(version=version, repo=repo, arch=arch),
            )
            logging.disable(logging.NOTSET)

            for new in index.packages:
                if new.name in ignore:
                    logging.info("Ignoring %r", new.name)
                    continue
                new.repo = repo
                pkg_newest(all_pkgs, new)
                pkg_newest(pkgs[arch], new)
                prov_newest(provs, new)

    return all_pkgs, pkgs, provs

def populate_packages(db, all_pkgs):
    logging.info("Building main package entries...")
    # Populate main packages first so we can reference them as origins to
    # subpackages
    mainpkgs = [
        apkweb.models.Pkg.from_index(i, None)
        for i in all_pkgs.values() if i.origin == i.name
    ]
    apkweb.models.Pkg.insertmany(db, mainpkgs)
    db.commit()
    del mainpkgs
    db.row_factory = apkweb.models.Pkg.factory
    rows = db.execute("SELECT * FROM packages WHERE origin IS NULL;")
    mainpkgs = {i.name: i for i in rows.fetchall()}
    del rows

    logging.info("Building subpackage entries...")
    subpkgs = []
    for pkg in all_pkgs.values():
        if pkg.origin == pkg.name:
            continue
        origin = mainpkgs[pkg.origin]
        subpkgs.append(apkweb.models.Pkg.from_index(pkg, origin.id))
    apkweb.models.Pkg.insertmany(db, subpkgs)
    db.commit()
    del mainpkgs
    del subpkgs

    rows = db.execute("SELECT * FROM packages;").fetchall()
    pkgids = {i.name: i.id for i in rows}
    return pkgids

def populate_versions(db, all_pkgs, pkgs, pkgids):
    logging.info("Building version table...")

    vers = collections.defaultdict(list)
    for arch in pkgs.keys():
        for pkg in pkgs[arch].values():
            vers[pkg.origin].append(apkweb.models.Version(
                None, pkgids[pkg.name], arch, pkg.version, None,
                pkg.size, pkg.commit, int(pkg._builddate),
            ))
        missing = set(all_pkgs.keys()) - set(pkgs[arch].keys())
        for name in missing:
            origin = all_pkgs[name].origin
            vers[origin].append(apkweb.models.Version(
                None, pkgids[name], arch, None, None,
                None, None, None,
            ))

    for name in vers:
        # Sort newest to oldest
        cmpvers = sorted(
            {i.version for i in vers[name] if i.version},
            key=apkweb.version.verkey,
            reverse=True,
        )
        for i, ver in enumerate(vers[name]):
            if not ver.version:
                continue
            vers[name][i] = ver._replace(
                vrank=cmpvers.index(ver.version),
            )

    vers = [j for i in vers.values() for j in i]
    apkweb.models.Version.insertmany(db, vers)
    db.commit()

def populate_deps(db, pkgs, pkgids, provs):
    logging.info("Building dependency tables...")

    cdeps = {}
    for arch in pkgs:
        for name, pkg in pkgs[arch].items():
            odeps = set()
            for dep in pkg.depends:
                odep = provs.get(atomize(dep))
                if not odep:
                    logging.warning(
                        "%s/%s depends on unknown %r",
                        arch, name, dep,
                    )
                    continue
                odep = pkgids.get(odep.name)
                if not odep:
                    logging.warning(
                        "%s/%s depends on %r with no ID",
                        arch, name, dep,
                    )
                    continue

                odeps.add(odep)

            pkg._depends = odeps

            if name not in cdeps:
                cdeps[pkgids[name]] = odeps
            else:
                cdeps[pkgids[name]] &= odeps

    adeps = []
    for arch in pkgs:
        for name, pkg in pkgs[arch].items():
            name = pkgids[name]
            new = pkg.depends - cdeps[name]
            if new:
                adeps.append((arch, name, new))

    cdeps = [apkweb.models.Dep(i, k) for i, j in cdeps.items() for k in j]
    adeps = [apkweb.models.Archdep(i[0], i[1], j) for i in adeps for j in i[2]]
    apkweb.models.Dep.insertmany(db, cdeps)
    apkweb.models.Archdep.insertmany(db, adeps)
    db.commit()

def populate_bugs(conf, db, pkgids):
    # TODO: match on version
    if not conf.get("bz_api_url"):
        return

    logging.info("Building bug tables...")
    field = conf["bz_field"]
    query = urllib.parse.urlencode([
        ("product", conf["bz_product"]),
        ("component", conf["bz_component"]),
        ("include_fields", ",".join((
            "id", "status", "summary", "keywords",
            field, "last_change_time",
        ))),
        ("status", conf.getlist(
            "bz_status", ["UNCONFIRMED", "CONFIRMED", "IN_PROGRESS"]
        )),
    ], doseq=True)
    request = urllib.request.Request(
        url=f"{conf['bz_api_url']}/bug?{query}",
        method="GET",
        headers={
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request) as response:
        bz_bugs = json.load(response)["bugs"]

    bugs = []
    buglinks = []
    for bug in bz_bugs:
        if not bug[field].strip():
            continue
        sdir = bug[field].split("/", maxsplit=1)
        if len(sdir) < 2:
            logging.error(
                "Bug %d: invalid %r: %s", bug["id"], field, bug[field],
            )
            continue
        repo, pkg = sdir
        pkgid = pkgids.get(pkg)
        if not pkgid:
            logging.warning(
                "Bug #%d: unknown package %r",
                bug["id"], bug[field],
            )
            continue

        bugs.append(apkweb.models.Bug(
            bug["id"], bug["summary"], ",".join(bug["keywords"]),
            datetime.datetime.strptime(
                bug["last_change_time"], BZ_DATE_FORMAT,
            ).strftime("%s"),
        ))
        buglinks.append(apkweb.models.Buglink(bug["id"], pkgid))

    apkweb.models.Bug.insertmany(db, bugs)
    apkweb.models.Buglink.insertmany(db, buglinks)
    db.commit()

def populate_merges(conf, db, pkgids):
    if not conf.get("gl_api_url"):
        return

    logging.info("Building merge request tables...")
    query = urllib.parse.urlencode({
        "state": "opened",
        "target_branch": conf["gl_branch"],
    }, doseq=True)
    url = f"{conf['gl_api_url']}/merge_requests?{query}"
    with urllib.request.urlopen(url) as response:
        gl_merges = json.load(response)

    merges = []
    mergelinks = []
    for merge in gl_merges:
        url = f"{conf['gl_api_url']}/merge_requests/{merge['iid']}/changes"
        with urllib.request.urlopen(url) as response:
            gl_changes = json.load(response)["changes"]

        sdirs = set()
        for change in gl_changes:
            for path in (change["old_path"], change["new_path"]):
                path = path.split("/", maxsplit=2)
                if len(path) != 3 or path[2] != "APKBUILD":
                    continue
                repo, pkg, _ = path
                sdirs.add((repo, pkg))

        matches = False
        for repo, pkg in sdirs:
            pkgid = pkgids.get(pkg)
            if not pkgid:
                logging.warning(
                    "MR #%d: new or unknown package '%s/%s'",
                    merge["iid"], repo, pkg,
                )
                continue

            if not matches:
                merges.append(apkweb.models.Merge(
                    merge["iid"], merge["title"], ",".join(merge["labels"]),
                    datetime.datetime.strptime(
                        merge["updated_at"], GL_DATE_FORMAT,
                    ).strftime("%s"),
                ))
                matches = True
            mergelinks.append(apkweb.models.Mergelink(merge["iid"], pkgid))

    apkweb.models.Merge.insertmany(db, merges)
    apkweb.models.Mergelink.insertmany(db, mergelinks)
    db.commit()

if __name__ == "__main__":
    opts = argparse.ArgumentParser(
        usage="python3 -m apkweb.build_db [options ...] VERSION [VERSION ...]",
    )
    opts.add_argument(
        "-d", "--output-directory", dest="dir",
        help="directory in which to write complete databases",
    )
    opts.add_argument(
        "-q", "--quiet", dest="loglevel",
        action="store_const", const="WARNING", default="INFO",
        help="only show warnings or errors",
    )
    opts.add_argument(
        "versions", metavar="VERSION", nargs="+",
        help="which repository versions to build databases for",
    )
    opts = opts.parse_args()
    opts.dir = Path(opts.dir).resolve() if opts.dir else Path.cwd()
    logging.basicConfig(level=opts.loglevel)

    for version in opts.versions:
        logging.info("Building %s database...", version)
        conf = apkweb.config(version)
        repos = conf.getmaplist("repos")
        db = init_db(opts.dir / f"{version}.sqlite")

        all_pkgs, pkgs, provs = pull_indices(conf, version)

        pkgids = populate_packages(db, all_pkgs)
        populate_versions(db, all_pkgs, pkgs, pkgids)
        populate_deps(db, pkgs, pkgids, provs)

        populate_bugs(conf, db, pkgids)
        populate_merges(conf, db, pkgids)

        db.execute("""
            INSERT INTO fts_packages(fts_packages)
            VALUES ('rebuild');
        """)
        db.close()
