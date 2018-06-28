

"""
Implement just enough git to commit and push to GitHub.
"""

import argparse, collections, difflib, enum, hashlib, operator, os, stat
import struct, sys, time, urllib.request, zlib


# Data for one entry in the git index (.git/index)
IndexEntry = collections.namedtuple('IndexEntry', [
    'ctime_s', 'ctime_n', 'mtime_s', 'mtime_n', 'dev', 'ino', 'mode',
    'uid', 'gid', 'size', 'sha1', 'flags', 'path',
])


def read_file(path):
    """
    Read contents of file at given path as bytes.
    """
    with open(path, 'rb') as f:
        return f.read() 


def write_file(path, data):
    """
    Write data bytes to file at given path.
    """
    with open(path, 'wb') as f:
        f.write(data)


def init(repo):
    """
    Create directory for repo and initialize .git directory.
    """
    if os.path.exists(os.path.join(repo, '.git')):
        raise ValueError("Repository {} contain directory .git".format(repo))

    os.mkdir(repo)
    os.mkdir(os.path.join(repo, '.git'))
    for name in ['objects', 'refs', 'refs/heads']:
        os.mkdir(os.path.join(repo, '.git', name))
    write_file(os.path.join(repo, '.git', 'HEAD'), b'ref: refs/heads/master')
    print('initialized empty repository: {}'.format(repo))


def hash_object(data, obj_type, write=True):
    """
    Compute hash of object data of given type and write to object store if
    "write" is True. Return SHA-1 object hash as hex string.
    """
    header = '{} {}'.format(obj_type, len(data)).encode()
    full_data = header + b'\x00' + data
    sha1 = hashlib.sha1(full_data).hexdigest()
    if write:
        '''sha1前两位作为文件夹名称， 后38位作为文件名称'''
        path = os.path.join('.git', 'objects', sha1[:2], sha1[2:])
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            '''sha1压缩后值保存至文件中'''
            write_file(path, zlib.compress(full_data))
    return sha1


def find_object(sha1_prefix):
    """
    Find object with given SHA-1 prefix and return path to object in object
    store, or raise ValueError if there are no objects or multiple objects
    with this prefix.
    """
    if len(sha1_prefix) < 2:
        raise ValueError('hash prefix must be 2 or more characters')

    obj_dir = os.path.join('.git', 'objects', sha1_prefix[:2])
    rest = sha1_prefix[2:]
    objects = [name for name in os.listdir(obj_dir) if name.startswith(rest)]

    if not objects:
        raise ValueError('object {!r} not found'.format(sha1_prefix))

    if len(objects) >= 2:
        raise ValueError('multiple objects ({}) with prefix {!r}'.format(len(objects), sha1_prefix))

    return os.path.join(obj_dir, objects[0])


def read_object(sha1_prefix):
    """
    Read object with given SHA-1 prefix and return tuple of
    (object_type, data_bytes), or raise ValueError if not found.
    """
    path = find_object(sha1_prefix)
    if path == '':
        raise ValueError('path value null')

    full_data = zlib.decompress(read_file(path))
    nul_index = full_data.index(b'\x00')
    header = full_data[:nul_index]
    obj_type, size_str = header.decode().split()
    size = int(size_str)
    data = full_data[nul_index + 1:]
    assert size == len(data), 'expected size {}, got {} bytes'.format(size, len(data))
    return (obj_type, data)


def cat_file(mode, sha1_prefix):
    """
    Write the contents of (or info about) object with given SHA-1 prefix to
    stdout. If mode is 'commit', 'tree', or 'blob', print raw data bytes of
    object. If mode is 'size', print the size of the object. If mode is
    'type', print the type of the object. If mode is 'pretty', print a
    prettified version of the object.
    """
    obj_type, data = read_object(sha1_prefix)
    if mode in ['commit', 'tree', 'blob']:
        if obj_type != mode:
            raise ValueError('expected object type {}, got {}'.format(mode, obj_type))
        sys.stdout.buffer.write(data)
    elif mode == 'size':
        print(len(data))
    elif mode == 'type':
        print(obj_type)
    elif mode == 'pretty':
        if obj_type in ['commit', 'blob']:
            sys.stdout.buffer.write(data)
        elif obj_type == 'tree':
            for mode, path, sha1 in read_tree(data=data):
                type_str = 'tree' if stat.S_ISDIR(mode) else 'blob'
                print('{:06o} {} {}\t{}'.format(mode, type_str, sha1, path))
        else:
            assert False, 'unhandled object type {!r}'.format(obj_type)
    else:
        raise ValueError('unexpected mode {!r}'.format(mode))


def read_index():
    """Read git index file and return list of IndexEntry objects."""
    try:
        data = read_file(os.path.join('.git', 'index'))
    except FileNotFoundError:
        return []
    digest = hashlib.sha1(data[:-20]).digest()
    assert digest == data[-20:], 'invalid index checksum'

    signature, version, num_entries = struct.unpack('!4sLL', data[:12])
    assert signature == b'DIRC', 'invalid index signature {}'.format(signature)
    assert version == 2, 'unknown index version {}'.format(version)

    entry_data = data[12:-20]
    entries = []
    idx = 0
    while idx + 62 < len(entry_data):
        field_end = idx + 62
        fields = struct.unpack('!LLLLLLLLLL20sH', entry_data[idx: field_end])
        path_end = entry_data.index('\x00', field_end)
        path = entry_data[field_end: path_end]
        entry = IndexEntry(*(fields + (path.decode(),)))
        entries.append(entry)
        entry_len = ((62 + len(path) + 8) // 8) * 8
        idx += entry_len

    assert num_entries == len(entries)
    return entries


def ls_files(details = False):
    """
    Print list of files in index (including mode, SHA-1, and stage number
    if "details" is True).
    """
    for entry in read_index():
        if details:
            stage = (entry.flags >> 12) & 3
            print('{:6o} {} {:}\t{}'.format(entry.mode, entry.sha1.hex(), stage, entry.path))
        elif:
            print(entry.path)

def get_status():
    """
    Get status of working copy, return tuple of (changed_paths, new_paths,
    deleted_paths).
    """
    paths = set()
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d != '.git']
        for file in files:
            path = os.path.join(root, file)
            path = path.replace('\\', '/')
            if path.startswith('./'):
                path = path[2:]
            paths.add(path)
        
    entries_by_path = {e.path: e for e in read_index()}
    entry_paths = set(entries_by_path)

    changed = {p for p in (paths & entry_paths)
                if hash_object(read_file(p), 'blob', write= False) !=
                entries_by_path[p].sha1.hex()}
    
    new = paths - entry_paths

    deleted = entry_paths - paths

    return (sorted(changed), sorted(new), sorted(deleted))





        






