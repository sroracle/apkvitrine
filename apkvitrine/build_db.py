# SPDX-License-Identifier: NCSA
# Copyright (c) 2020 Max Rees
# See LICENSE for more information.
import argparse       # ArgumentParser
import collections    # defaultdict
import datetime       # datetime
import json           # load
import logging
import pkgutil        # get_data
import sqlite3        # connect
import urllib.parse   # urlencode
import urllib.request # Request, urlopen
from pathlib import Path

from apkkit.base.index import Index

import apkvitrine
import apkvitrine.models
import apkvitrine.version # APK_OPS, is_older, verkey

GL_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
BZ_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

def init_db(name):
    try:
        Path(name).unlink()
    except FileNotFoundError:
        pass
    schema = pkgutil.get_data("apkvitrine", "data/schema.sql").decode("utf-8")
    db = sqlite3.connect(str(name))
    db.executescript(schema)
    return db

def atomize(spec):
    if not any(i in spec for i in apkvitrine.version.APK_OPS):
        return spec

    for op in apkvitrine.version.APK_OPS:
        try:
            name, _ver = spec.split(op, maxsplit=1)
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
    elif apkvitrine.version.is_older(old.version, new.version):
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
                url=conf["index"].format(version=version, repo=repo, arch=arch),
            )
            logging.disable(logging.NOTSET)

            for new in index.packages:
                if new.name in ignore:
                    logging.info("Ignoring %r", new.name)
                    continue
                if new.origin in ignore:
                    logging.info(
                        "Pruning %r from ignored origin %r",
                        new.name, new.origin,
                    )
                    continue
                new.repo = repo
                pkg_newest(all_pkgs, new)
                pkg_newest(pkgs[arch], new)
                prov_newest(provs, new)

    return all_pkgs, pkgs, provs

def populate_packages(conf, db, all_pkgs):
    logging.info("Building main package entries...")
    sdir_custom = conf.getmap("startdirs")

    # Populate main packages first so we can reference them as origins to
    # subpackages
    mainpkgs = [
        apkvitrine.models.Pkg.from_index(
            i, sdir_custom.get(i.name, f"{i.repo}/{i.name}"), None,
        ) for i in all_pkgs.values() if i.origin == i.name
    ]
    main_startdirs = {i.startdir: i.name for i in mainpkgs}
    apkvitrine.models.Pkg.insertmany(db, mainpkgs)
    db.commit()
    del mainpkgs
    db.row_factory = apkvitrine.models.Pkg.factory
    rows = db.execute("SELECT * FROM packages WHERE origin IS NULL;")
    mainpkgs = {i.name: i for i in rows.fetchall()}
    del rows

    logging.info("Building subpackage entries...")
    subpkgs = []
    for pkg in all_pkgs.values():
        if pkg.origin == pkg.name:
            continue
        origin = mainpkgs[pkg.origin]
        subpkgs.append(apkvitrine.models.Pkg.from_index(
            pkg, origin.startdir, origin.id,
        ))
    apkvitrine.models.Pkg.insertmany(db, subpkgs)
    db.commit()
    del mainpkgs
    del subpkgs

    rows = db.execute("SELECT * FROM packages;").fetchall()
    pkgids = {i.name: i.id for i in rows}
    return pkgids, main_startdirs

def populate_versions(db, all_pkgs, pkgs, pkgids):
    logging.info("Building version table...")

    vers = collections.defaultdict(list)
    for arch in pkgs.keys():
        for pkg in pkgs[arch].values():
            vers[pkg.origin].append(apkvitrine.models.Version(
                None, pkgids[pkg.name], arch, pkg.version, None,
                pkg.size, pkg.commit, int(pkg._builddate),
            ))
        missing = set(all_pkgs.keys()) - set(pkgs[arch].keys())
        for name in missing:
            origin = all_pkgs[name].origin
            vers[origin].append(apkvitrine.models.Version(
                None, pkgids[name], arch, None, None,
                None, None, None,
            ))

    for name in vers:
        # Sort newest to oldest
        cmpvers = sorted(
            {i.version for i in vers[name] if i.version},
            key=apkvitrine.version.verkey,
            reverse=True,
        )
        for i, ver in enumerate(vers[name]):
            if not ver.version:
                continue
            vers[name][i] = ver._replace(
                vrank=cmpvers.index(ver.version),
            )

    vers = [j for i in vers.values() for j in i]
    apkvitrine.models.Version.insertmany(db, vers)
    db.commit()

def populate_deps(db, pkgs, pkgids, provs):
    logging.info("Building dependency tables...")

    # Common deps
    cdeps = {}
    # Missing deps
    mdeps = []

    for arch in pkgs:
        for name, pkg in pkgs[arch].items():
            odeps = set()
            for dep in pkg.depends:
                odep = provs.get(atomize(dep))
                if not odep and dep.startswith("!"):
                    continue
                if not odep:
                    logging.warning(
                        "%s/%s depends on unknown %r",
                        arch, name, dep,
                    )
                    mdeps.append((pkgids[name], arch, dep))
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

    # Arch-specific deps
    adeps = []
    for arch in pkgs:
        for name, pkg in pkgs[arch].items():
            name = pkgids[name]
            new = pkg.depends - cdeps[name]
            if new:
                adeps.append((arch, name, new))

    cdeps = [apkvitrine.models.Dep(i, k) for i, j in cdeps.items() for k in j]
    adeps = [apkvitrine.models.Archdep(i[0], i[1], j) for i in adeps for j in i[2]]
    mdeps = [apkvitrine.models.Missingdep(*i) for i in mdeps]
    apkvitrine.models.Dep.insertmany(db, cdeps)
    apkvitrine.models.Archdep.insertmany(db, adeps)
    apkvitrine.models.Missingdep.insertmany(db, mdeps)
    db.commit()

def populate_bugs(conf, db, pkgids, main_startdirs):
    # TODO: match on version
    if not conf.get("bz.api"):
        return

    logging.info("Building bug tables...")
    field = conf["bz.field"]
    query = urllib.parse.urlencode([
        ("product", conf["bz.product"]),
        ("component", conf["bz.component"]),
        ("include_fields", ",".join((
            "id", "status", "summary", "keywords",
            field, "last_change_time",
        ))),
        ("status", conf.getlist(
            "bz.status", ["UNCONFIRMED", "CONFIRMED", "IN_PROGRESS"]
        )),
    ], doseq=True)
    request = urllib.request.Request(
        url=f"{conf['bz.api']}/bug?{query}",
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
        sdirs = bug[field].split(", ")
        if not any(sdirs):
            continue

        for sdir in sdirs:
            if sdir.count("/") == 0:
                pkg = sdir
            elif sdir.count("/") == 1:
                _, pkg = sdir.split("/", maxsplit=1)
            else:
                logging.error(
                    "Bug #%d: invalid %r: %r", bug["id"], field, sdir,
                )
                continue

            pkgid = pkgids.get(pkg)
            if not pkgid and main_startdirs.get(sdir):
                pkg_new = main_startdirs[sdir]
                logging.info(
                    "Bug #%d: %r -> %r",
                    bug["id"], sdir, pkg_new,
                )
                pkgid = pkgids.get(pkg_new)
            if not pkgid:
                logging.warning(
                    "Bug #%d: unknown package %r",
                    bug["id"], sdir,
                )
                continue
            buglinks.append(apkvitrine.models.Buglink(bug["id"], pkgid))

        bugs.append(apkvitrine.models.Bug(
            bug["id"], bug["summary"], ",".join(bug["keywords"]),
            datetime.datetime.strptime(
                bug["last_change_time"], BZ_DATE_FORMAT,
            ).timestamp(),
        ))

    apkvitrine.models.Bug.insertmany(db, bugs)
    apkvitrine.models.Buglink.insertmany(db, buglinks)
    db.commit()

def populate_merges(conf, db, pkgids, main_startdirs):
    if not conf.get("gl.api"):
        return

    logging.info("Building merge request tables...")
    query = urllib.parse.urlencode({
        "state": "opened",
        "target_branch": conf["gl.branch"],
    }, doseq=True)
    url = f"{conf['gl.api']}/merge_requests?{query}"
    with urllib.request.urlopen(url) as response:
        gl_merges = json.load(response)

    merges = []
    mergelinks = []
    for merge in gl_merges:
        url = f"{conf['gl.api']}/merge_requests/{merge['iid']}/changes"
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
            sdir = f"{repo}/{pkg}"
            pkgid = pkgids.get(pkg)
            if not pkgid and main_startdirs.get(sdir):
                pkg_new = main_startdirs[sdir]
                logging.info(
                    "MR #%d: %r -> %r",
                    merge["iid"], sdir, pkg_new,
                )
                pkgid = pkgids.get(pkg_new)
            if not pkgid:
                logging.warning(
                    "MR #%d: new or unknown package %r",
                    merge["iid"], sdir,
                )
                continue

            if not matches:
                merges.append(apkvitrine.models.Merge(
                    merge["iid"], merge["title"], ",".join(merge["labels"]),
                    datetime.datetime.strptime(
                        merge["updated_at"], GL_DATE_FORMAT,
                    ).timestamp(),
                ))
                matches = True
            mergelinks.append(apkvitrine.models.Mergelink(merge["iid"], pkgid))

    apkvitrine.models.Merge.insertmany(db, merges)
    apkvitrine.models.Mergelink.insertmany(db, mergelinks)
    db.commit()

if __name__ == "__main__":
    opts = argparse.ArgumentParser(
        usage="python3 -m apkvitrine.build_db [options ...] VERSION [VERSION ...]",
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
        conf = apkvitrine.config(version)
        repos = conf.getmaplist("repos")
        db = init_db(opts.dir / f"{version}.sqlite")

        all_pkgs, pkgs, provs = pull_indices(conf, version)

        pkgids, main_startdirs = populate_packages(conf, db, all_pkgs)
        populate_versions(db, all_pkgs, pkgs, pkgids)
        del all_pkgs
        populate_deps(db, pkgs, pkgids, provs)
        del provs

        populate_bugs(conf, db, pkgids, main_startdirs)
        populate_merges(conf, db, pkgids, main_startdirs)
