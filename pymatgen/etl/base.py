"""
Interfaces and utilities to perform
Extract-Transform-Load operations 
between MongoDB collections.
"""
__author__ = "Dan Gunter"
__copyright__ = "Copyright 2012, The Materials Project"
__maintainer__ = "Dan Gunter"
__email__ = "dkgunter@lbl.gov"
__date__ = "29 May 2012"

import importlib
from StringIO import StringIO
import sys
import yaml
import warnings
try:
    import pymongo
except ImportError:
    pymongo = None
    warnings.warn("Failed to import 'pymongo'. "
    "Database operations will fail with NoneType error.")

# Constants
STDIN, STDOUT = "STDIN", "STDOUT"

class ETLError(Exception):
    def __init__(self, src=None, tgt=None, msg=None, base_exc="None"):
        if src is None:
            s = "Failed: {m}. Base exception: {e}".format(m=msg, e=base_exc)
        else:
            s = "Failed for source {s} -> target {t}. " \
                "Base exception: {e}".format(s=src, t=tgt, e=base_exc)
        Exception.__init__(self, s)

class CollectionOutputStream(object):
    """Wrapper to treat MongoDB collection as an output stream
    """
    def __init__(self, coll, safe=False, batch=-1):
        self._coll = coll
        self._safe = safe
        self._batch_data = [ ]
        self.set_batch_size(batch)
        
    def _insert(self, data):
        if self._batched:
            self._batch_data.append(data)
            if len(self._batch_data) >= self._batch_sz:
                r = self.flush()
        else:
            r = self._coll.save(data, sage=self._safe)
        return r

    write = _insert

    def flush(self):
        """Flush all batched data.
        """
        if self._batched:
            r = self._coll.save(self._batch_data, safe=self._safe)
            self._batch_data = [ ]
        return r

    def set_batch_size(self, n):
        """Set the size of batches to use.
        """
        if self._batch_data:
            self.flush()
        if batch > 0:
            self._batched = True
            self._batch_sz = batch
        else:
            self._batched = False
        
    def set_safe(self, tf):
        """Set whether inserts are done in safe-mode, or not.
        """
        self._safe = tf

class CollectionInputStream(object):
    def __init__(self, coll, expr={}):
        self._coll = coll
        self._strm = self._coll.find(expr)
        
    def __iter__(self):
        return self._strm

class FileInputStream(object):
    def __init__(self, f):
        import simplejson as json
        self.json = json
        self._f = f
        
    def __iter__(self):
        return self
        
    def next(self):
        try:
            obj = self.json.load(self._f)
        except self.json.JSONDecodeError:
            raise StopIteration()
        return obj

class FileOutputStream(object):
    def __init__(self, f):
        import simplejson as json
        self.json = json
        self._f = f
        
    def write(self, obj):
        return self._f.write(self.json.dumps(obj))

    def flush(self):
        self._f.flush()

class ETLBase(object):
    """Base class for extract-transform-load.
    """
    
    # Name of section for 'extra' data from source
    EXTRA = "external"

    def __init__(self, src=None, tgt=None):
        """Create with source and target MongoDB collections.
        """
        self.src = src
        self.tgt = tgt
        
    def extract_transform_load(self):
        """Subclasses must override this to actually
        perform the operation.
        
        The transformation will use the instance vars:
        * src - Source collection
        * tgt - Target collection
          
        Returns: None
        Raises: Any Exception
        """
        return None

class ETLRunner:
    """Working from a YAML configuration file,
    perform arbitrary extract-transform-load (ETL) operations
    from one or more source collections to a target collection.
    """
    
    # Constants
    NO_VAL = "___none___"
    IO_FILE, IO_MONGO = 0, 1 # I/O type constants
    # fields
    F_FILE, F_COLL, F_DB = "file", "collection", "db"
    F_MOD, F_CLS = "module", "class"
    
    # Configuration layout with defaults.
    # If default is 'None' then the value must be provided in the
    # configuration file.
    CONF = {
        "sources" : {
            F_FILE : NO_VAL,
            F_COLL : NO_VAL,
            F_MOD : None,
            F_CLS : "ETL",
            "param" : { }
        },
        "target": {
            F_FILE : NO_VAL,
            "host" : "localhost",
            "port" : 27017,
            "user" : "",
            "password" : "",
            F_DB : NO_VAL,
            F_COLL : NO_VAL
        },
    }

    def __init__(self, conf):
        """Configure with YAML from file or string, or
        a pre-configured dictionary.
        
        Raises: ValueError, yaml.YAMLError
        """
        if hasattr(conf, "read"):
            self._conf = yaml.load(conf)
        elif hasattr(conf, "get"):
            self._conf = conf
        else:
            self._conf = yaml.load(StringIO(conf))
        self.target, self.sources = None, [ ]
        self.target_io_type, self.source_io_types = None, [ ]
        for section in self.CONF:
            if not section in self._conf:
                raise ValueError("Missing section: {}".format(section))
            if section == "target":
                contents = self._conf[section]
                values = self._get_values(section, contents)
                if values[self.F_FILE] is self.NO_VAL:
                    if values[self.F_COLL] is self.NO_VAL or \
                        values[self.F_COLL] is self.NO_VAL:
                        raise ValueError("Target must have either '{0}' or "
                        "'{1}' + '{2}'"
                        .format(self.F_FILE, self.F_COLL, self.F_DB))
                    self.target_io_type = self.IO_MONGO
                else:
                    self.target_io_type = self.IO_FILE
                self.target = values
            elif section == "sources":
                for contents in self._conf[section]:
                    values = self._get_values(section, contents)
                    if values[self.F_FILE] is self.NO_VAL:
                        if values[self.F_COLL] is self.NO_VAL:
                            raise ValueError("Source {2} must have either "
                            "'{0}' or '{1}'"
                            .format(self.F_FILE, self.F_COLL,
                            values[self.F_MOD]))
                        self.source_io_types.append(self.IO_MONGO)
                    else:
                        self.source_io_types.append(self.IO_FILE)
                    self.sources.append(values)
            else:
                raise ValueError("Unknown section: {}".format(section))

    # Helpers for __init__

    def _get_values(self, section, data):
        v = { }
        for key, default in self.CONF[section].iteritems():
            uval = data.get(key, default)
            if uval is None:
                raise ValueError("Missing key: {sec}.{key}".format(
                sec=section, key=key))
            v[key] = uval
        return v
                  
    def __len__(self):
        """Number of source collections"""
        return len(self.sources)
        
    # Run

    def _open_etl(self, fname, mode='r', which="src"):
        try:
            if fname == STDIN:
                obj = sys.stdin
            elif fname == STDOUT:
                obj = sys.stdout
            else:
                obj = open(fname, mode)
        except IOError, err:
            kw = {which: fname, base_exc:err}
            raise ETLError(**kw)
        return obj
        
    def run(self):
        """Run all the ETL operations.
        
        Raises: ETLError
        Returns: Number run
        """
        dbconn = None
        if self.target_io_type is self.IO_MONGO:
            dbconn = self._connect()
            target = CollectionOutputStream(
                        dbconn[self.target[self.F_COLL]])                        
        elif self.target_io_type is self.IO_FILE:
            target = FileOutputStream(self._open_etl(self.target[self.F_FILE],
                                      mode='w', which='tgt'))
        else:
            raise ETLError(src="none", tgt="none",
                           base_exc="Internal error: "
                           "Unknown target I/O type {0}"
                           .format(self.target_io_type))
        n = 0
        for s, iot in zip(self.sources, self.source_io_types):
            if iot is self.IO_MONGO:
                source = CollectionInputStream(dbconn[s["collection"]])
            elif iot is self.IO_FILE:
                source = FileInputStream(self._open_etl(s[self.F_FILE]))
            else:
                raise ETLError(src=s, tgt="none",
                               base_exc="Internal error: "
                               "Unknown source I/O type {0}".format(iot))
            try:
                etl_mod = self._load_module(s["module"])
            except ImportError, err:
                raise ETLError(src=source, tgt=target, base_exc=err)                
            etl_cls = getattr(etl_mod, s["class"])
            etl_param = s["param"]
            etl = etl_cls(src=source, tgt=target, **etl_param)
            try:
                etl.extract_transform_load()
            except ETLError:
                raise
            except Exception, err:
                raise ETLError(src=source, tgt=target, base_exc=err)
            n += 1
        return n
    # helpers for run()

    def _load_module(self, mod_name):
        """Dynamically load a Python module.
        
        Returns: module object
        Raises: ImportError
        """
        if mod_name[0] == ".":
            # perform relative imports from pymatgen package
            mod = importlib.import_module(mod_name, "pymatgen")
        else:
            mod = importlib.import_module(mod_name)
        return mod

    def _connect(self):
        """Connect and authorize to MongoDB
        
        Returns: DB connection obj
        Raises: ETLError
        """
        cfg = self.target
        host, port, db = cfg["host"], cfg["port"], cfg["db"]
        if cfg["user"]:
            user, passwd = cfg["user"], cfg["password"]
            uri = ("mongodb://{u}:{w}@{h}:{p}/{d}"
                   .format(u=user, w=passwd, h=host, p=port, d=db))
        else:
            uri = "mongodb://{h}:{p}".format(h=host, p=port)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                conn = pymongo.Connection(uri)
        except pymongo.errors.ConnectionFailure, err:
            raise ETLError(msg="Connect to {h}:{p}/{d}"
                            .format(h=host, p=port, d=db, base_exc=err))
        dbconn = conn[db]
        return dbconn