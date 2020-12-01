PRAGMA foreign_keys = ON;

CREATE TABLE packages (
  id INTEGER PRIMARY KEY,
  repo TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  url TEXT,
  license TEXT,
  origin INTEGER REFERENCES packages(id),
  maintainer TEXT,
  size INTEGER,
  updated INTEGER
);

CREATE TABLE versions (
  id INTEGER PRIMARY KEY,
  package NOT NULL REFERENCES packages(id),
  arch TEXT NOT NULL,
  version TEXT,
  vrank INTEGER,
  size INTEGER,
  revision TEXT,
  created INTEGER
);

CREATE TRIGGER max_size
AFTER INSERT ON versions FOR EACH ROW WHEN
  NEW.size > (SELECT IFNULL(size, -1) FROM packages WHERE id = NEW.package)
BEGIN
  UPDATE packages SET size = NEW.size WHERE id = NEW.package;
END;

CREATE TRIGGER pkg_created
AFTER INSERT ON versions FOR EACH ROW WHEN
  NEW.created > (SELECT IFNULL(updated, -1) FROM packages WHERE id = NEW.package)
BEGIN
  UPDATE packages SET updated = NEW.created WHERE id = NEW.package;
END;

CREATE TABLE deps (
  rdep NOT NULL REFERENCES packages(id),
  dep NOT NULL REFERENCES packages(id)
);

CREATE TABLE archdeps (
  arch TEXT NOT NULL,
  rdep NOT NULL REFERENCES packages(id),
  dep NOT NULL REFERENCES packages(id)
);

CREATE TABLE bugs (
  id INTEGER PRIMARY KEY,
  summary TEXT NOT NULL,
  tags TEXT,
  updated INTEGER NOT NULL
);

CREATE TABLE buglinks (
  bug NOT NULL REFERENCES bugs(id),
  package NOT NULL REFERENCES packages(id)
);

CREATE TRIGGER pkg_bugged
AFTER INSERT ON buglinks FOR EACH ROW WHEN
  (SELECT updated FROM bugs WHERE id = NEW.bug)
  >
  (SELECT IFNULL(updated, -1) FROM packages WHERE id = NEW.package)
BEGIN
  UPDATE packages SET updated = (SELECT updated FROM bugs WHERE id = NEW.bug)
  WHERE id = NEW.package;
END;

CREATE TABLE merges (
  id INTEGER PRIMARY KEY,
  summary TEXT NOT NULL,
  tags TEXT,
  updated INTEGER NOT NULL
);

CREATE TABLE mergelinks (
  merge NOT NULL REFERENCES merges(id),
  package NOT NULL REFERENCES packages(id)
);

CREATE TRIGGER pkg_merged
AFTER INSERT ON mergelinks FOR EACH ROW WHEN
  (SELECT updated FROM merges WHERE id = NEW.merge)
  >
  (SELECT IFNULL(updated, -1) FROM packages WHERE id = NEW.package)
BEGIN
  UPDATE packages SET updated = (SELECT updated FROM merges WHERE id = NEW.merge)
  WHERE id = NEW.package;
END;
