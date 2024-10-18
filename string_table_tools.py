import argparse
import codecs
import re
import struct
import sys
import os
import subprocess

'''
Alternatives:
https://github.com/MinshuG/pyUE4Parse (python parser combine)
https://gist.github.com/andyneff/314172b5bdcb981c030ff0aa6bf2fa4a (simple python parser)
https://github.com/AstroTechies/PyPAKParser
https://github.com/AstroTechies/unrealmodding (rust parser combine)

'''

class Colorize:
    '''Colorize output in console (ANSI escape codes)'''

    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    ENDC = '\033[0m'

    enable = False


    def colorize(text: str, color: str = OKGREEN) -> str:
        '''Colorize full text with specified color'''
        if Colorize.enable:
            return color + text + Colorize.ENDC
        else:
            return text


    def colorize_pattern(text: str, pattern: str, ignore_case: bool = False, color: str = OKGREEN) -> str:
        '''Colorize text matching pattern with specified color'''
        if Colorize.enable:
            return re.sub(pattern, Colorize.colorize(r'\g<0>', color), text, flags=re.IGNORECASE if ignore_case else 0)
        else:
            return text

    def print(text: str, color: str = OKGREEN):
        '''Print text with specified color'''
        if Colorize.enable:
            print(Colorize.colorize(text, color))
        else:
            print(text)


class AssetReader:
    '''UE asset binary reader'''

    class Meta:
        file_version: int
        license_version: int
        ue_version: int
        cook_version: int
        asset_type: int
        data_offset: int
        path: str
        asset_guid: str

        def __init__(self, file_version: int, license_version: int, ue_version: int, cook_version: int, asset_type: int, data_offset: int, path: str, asset_guid: str):
            self.file_version = file_version
            self.license_version = license_version
            self.ue_version = ue_version
            self.cook_version = cook_version
            self.asset_type = asset_type
            self.data_offset = data_offset
            self.path = path
            self.asset_guid = asset_guid

    __data: bytes
    __cursor: int = 0

    def __init__(self, data: bytes):
        '''Initialize reader with data'''
        self.__data = data

    def validate(self):
        magic_number = self.read_uint()
        if magic_number != 0x9E2A83C1:
            raise ValueError("Not a valid .uasset file")
        self.reset()

    def get_asset_meta(self) -> Meta:
        '''Detect asset type'''
        self.skip(4) # skip magic number
        file_version = self.read_int()
        license_version = self.read_uint()
        ue_version = self.read_uint()

        cook_version = self.read_uint()
        if cook_version == 0 and file_version != -8:
            self.skip(-4) # Not -8 file version has different data, return back

        self.skip(4) # skip some data
        asset_type = self.read_uint()

        find_int = self.find_int(40)
        self.reset(find_int + 4) # skip some data

        data_offset = self.read_uint()
        path = self.read_string()
        self.skip(12) # skip some data

        v = self.read_int()
        if v == 0: # Not -8 file version has different data
            self.skip(4)
        else:
            self.skip(-4) # return back

        asset_guid = self.read_string()
        self.reset()
        return AssetReader.Meta(file_version, license_version, ue_version, cook_version, asset_type, data_offset, path, asset_guid)


    def read_int(self) -> int:
        '''Read 4 bytes as int'''
        value = struct.unpack("i", self.__data[self.__cursor:self.__cursor+4])[0]
        self.__cursor += 4
        return value

    def read_uint(self) -> int:
        '''Read 4 bytes as int'''
        value = struct.unpack("I", self.__data[self.__cursor:self.__cursor+4])[0]
        self.__cursor += 4
        return value

    def read_string(self) -> str:
        '''Read string with variadic length'''
        count = self.read_int()
        if count < 0:
            count = -count * 2 # UE magic for wide chars
            encoding = 'utf-16'
        else:
            encoding = 'ascii'
        value = struct.unpack("%ds" % (count), self.__data[self.__cursor:self.__cursor+count])[0].decode(encoding)
        if value.endswith("\x00"):
            value = value[:-1]
        else:
            raise AssetParseException("String not null terminated")
        self.__cursor += count
        return value


    def skip(self, count: int):
        '''Skip specified number of bytes'''
        self.__cursor += count

    def reset(self, position: int = 0):
        '''Reset cursor to start'''
        self.__cursor = position

    def find_int(self, value: int) -> int:
        '''Find first occurrence of value in data'''
        return self.__data.find(struct.pack("i", value), self.__cursor)

    def find(self, value: str) -> int:
        '''Find first occurrence of value in data'''
        return self.__data.find(value, self.__cursor)

    def find_last(self, value: str) -> int:
        '''Find last occurrence of value in data'''
        return self.__data.rfind(value, self.__cursor)


class AssetParseException(Exception):
    '''Exception raised when parsing asset fails'''
    pass


def parse_meta(data: bytes) -> AssetReader.Meta:
    '''Parse asset meta data and return AssetReader.Meta'''
    try:
        content = AssetReader(data)
        content.validate()
        return content.get_asset_meta()
    except Exception as e:
        raise AssetParseException(e)


def parse_string_table(data: bytes) -> list[tuple[str, str]]: # save order of keys
    '''Parse StringTable asset and return list of tuples (key, value)'''
    out = []

    try:
        content = AssetReader(data)
        content.validate()

        meta = content.get_asset_meta()
        if meta.asset_type != 0x1:
            raise AssetParseException("Not a suitable string table")

        content.reset(meta.data_offset) # move to data
        content.skip(28) # some binary data

        asset_guid = content.read_string() # asset guid
        if asset_guid != meta.asset_guid:
            raise AssetParseException("Asset code mismatch")

        content.skip(12) # some binary data
        content.read_string() # table name

        count = content.read_int() # number of entries
        for _ in range(count):
            key = content.read_string()
            value = content.read_string()
            out.append((key, value))

    except Exception as e:
        raise AssetParseException(e)

    return out


def parse_string_table_file(file_in) -> list[tuple[str, str]]: # save order of keys
    '''Parse StringTable asset and return list of tuples (key, value)'''
    with open(file_in, mode='rb') as file:
        return parse_string_table(file.read())


def convert_to_csv(tuples: list[tuple[str, str]]) -> list[str]:
    out = []
    out.append("Key,SourceString")
    for tuple in tuples:
        out.append("\"%s\",\"%s\"" % (tuple[0], tuple[1]))
    out.append("")
    return out


def save_as_utf16(content: list[str], file_out):
    with codecs.open(file_out, mode='w', encoding='utf-16') as file:
        for line in content:
            file.write(line + "\n")


def compare(a: list[tuple[str, str]], b: list[tuple[str, str]]):
    dict_a = dict(a)
    dict_b = dict(b)

    for key in dict_a:
        if key in dict_b:
            if dict_a[key] != dict_b[key]:
                Colorize.print("!: %s = \"%s\" != \"%s\"" % (key, dict_a[key], dict_b[key]), Colorize.WARNING)

    for key in dict_a:
        if key not in dict_b:
            Colorize.print("-: %s = \"%s\"" % (key, dict_a[key]), Colorize.FAIL)

    for key in dict_b:
        if key not in dict_a:
            Colorize.print("+: %s = \"%s\"" % (key, dict_b[key]), Colorize.OKGREEN)


def compare_inside_git_repo(work_dir: str, file: str, revision_a: str, revision_b: str):
    '''Compare file inside git repo'''

    def read_local(work_dir: str, file: str) -> bytes:
        return open(os.path.join(work_dir, file), mode='rb').read()
    def read_git(work_dir: str, file: str, revision: str) -> bytes:
        posix_file = file.replace(os.path.sep, '/')
        data = subprocess.check_output(["git", "cat-file", "--filters", "%s:%s" % (revision, posix_file)], cwd=work_dir, stderr=subprocess.DEVNULL)
        sys.stdout.flush() # fix colorize output
        return data

    data_a = read_local(work_dir, file) if revision_a == "LOCAL" else read_git(work_dir, file, revision_a)
    data_b = read_local(work_dir, file) if revision_b == "LOCAL" else read_git(work_dir, file, revision_b)
    data_a = parse_string_table(data_a)
    data_b = parse_string_table(data_b)
    compare(data_a, data_b)


def search(data: list[tuple[str, str]], pattern: str, ignore_case: bool = False, search_only_values: bool = False, exclude_keys: list[str] = [], include_keys: list[str] = []):
    out = []
    for tuple in data:
        if include_keys is not None and len(include_keys) > 0 and not any(re.search(p, tuple[0], re.IGNORECASE if ignore_case else 0) for p in include_keys):
            continue
        if exclude_keys is not None and len(exclude_keys) > 0 and any(re.search(p, tuple[0], re.IGNORECASE if ignore_case else 0) for p in exclude_keys):
            continue
        if (not search_only_values and re.search(pattern, tuple[0], re.IGNORECASE if ignore_case else 0)) or re.search(pattern, tuple[1], re.IGNORECASE if ignore_case else 0):
            out.append(tuple)
    return out


def search_in_file(file: str, pattern: str, ignore_case: bool = False, search_only_values: bool = False, exclude_keys: list[str] = [], include_keys: list[str] = []):
    data = parse_string_table_file(file)
    results = search(data, pattern, ignore_case, search_only_values, exclude_keys, include_keys)
    try:
        if len(results) > 0:
            Colorize.print("Results in \"%s\":" % file, Colorize.WARNING)
            for result in results:
                key = result[0] if search_only_values else Colorize.colorize_pattern(result[0], pattern, ignore_case)
                value = Colorize.colorize_pattern(result[1], pattern, ignore_case)
                print("  %s = \"%s\"" % (key, value))
    except AssetParseException as e:
        Colorize.print("Error while parsing file \"%s\" with \"%s\"" % (file, e))


def search_in_folder(folder: str, pattern: str, ignore_case: bool = False, search_only_values: bool = False, exclude_keys: list[str] = [], include_keys: list[str] = []):
    for file in os.listdir(folder):
        if file.endswith(".uasset"):
            file_path = os.path.join(folder, file)
            search_in_file(file_path, pattern, ignore_case, search_only_values, exclude_keys, include_keys)


def main(args):
    parser = argparse.ArgumentParser(description='UE StringTable tools')
    subparsers = parser.add_subparsers(dest='command', metavar="command", help='Command to execute (convert, cat, search, compare, compare-git, meta)', required=True)
    convert_parser = subparsers.add_parser('convert')
    convert_parser.add_argument('file_in', metavar="input-file", type=str, help='Input file')
    convert_parser.add_argument('file_out', metavar="output-file", type=str, help='Output file')
    cat_parser = subparsers.add_parser('cat')
    cat_parser.add_argument('file', metavar="file", type=str, help='File to cat')
    cat_parser.add_argument('--colorize', dest='colorize_output', action='store_true', help='Colorize output')
    search_parser = subparsers.add_parser('search')
    search_parser.add_argument('path', metavar="path", type=str, help='Path to file or folder with string tables')
    search_parser.add_argument('pattern', metavar="pattern", type=str, help='Regex pattern')
    search_parser.add_argument('--ignore-case', dest='ignore_case', action='store_true', help='Search ignore case')
    search_parser.add_argument('--exclude-keys', dest='exclude_keys', type=str, nargs='+', help='Exclude keys regex patterns')
    search_parser.add_argument('--include-keys', dest='include_keys', type=str, nargs='+', help='Include keys regex patterns')
    search_parser.add_argument('--search-only-values', dest='search_only_values', action='store_true', help='Search only in values')
    search_parser.add_argument('--colorize', dest='colorize_output', action='store_true', help='Colorize output')
    compare_parser = subparsers.add_parser('compare')
    compare_parser.add_argument('file_a', metavar="first-file", type=str, help='First input file')
    compare_parser.add_argument('file_b', metavar="second-file", type=str, help='Second input file')
    compare_parser.add_argument('--colorize', dest='colorize_output', action='store_true', help='Colorize output')
    compare_git_parser = subparsers.add_parser('compare-git')
    compare_git_parser.add_argument('work_dir', metavar="repo-dir", type=str, help='Git work dir')
    compare_git_parser.add_argument('file', metavar="file", type=str, help='File to compare')
    compare_git_parser.add_argument('revision_a', metavar="revision-A", type=str, help='Revision A (LOCAL to use local file)')
    compare_git_parser.add_argument('revision_b', metavar="revision-B", type=str, help='Revision B (LOCAL to use local file)')
    compare_git_parser.add_argument('--colorize', dest='colorize_output', action='store_true', help='Colorize output')
    get_meta_parser = subparsers.add_parser('meta')
    get_meta_parser.add_argument('file', metavar="file", type=str, help='Input file')
    get_meta_parser.add_argument('--colorize', dest='colorize_output', action='store_true', help='Colorize output')
    p = parser.parse_args(args)

    if p.colorize_output:
        Colorize.enable = True

    if (p.command == 'convert'):
        Colorize.print("Converting \"%s\" -> \"%s\"" % (p.file_in, p.file_out), Colorize.OKCYAN)
        data = parse_string_table_file(p.file_in)
        data = convert_to_csv(data)
        save_as_utf16(data, p.file_out)

    if (p.command == 'cat'):
        Colorize.print("Catting \"%s\"" % p.file, Colorize.OKCYAN)
        data = open(p.file, mode='rb').read()
        data = parse_string_table(data)
        for tuple in data:
            print("%s = \"%s\"" % (Colorize.colorize(tuple[0], Colorize.WARNING), Colorize.colorize(tuple[1], Colorize.OKGREEN)))

    if (p.command == 'search'):
        Colorize.print("Searching \"%s\" in \"%s\" (ignore case = %s, search only in values = %s, exclude keys = %s, include keys = %s)" % (p.pattern, p.path, p.ignore_case, p.search_only_values, p.exclude_keys, p.include_keys), Colorize.OKCYAN)
        if os.path.isdir(p.path):
            search_in_folder(p.path, p.pattern, p.ignore_case, p.search_only_values, p.exclude_keys, p.include_keys)
        else:
            search_in_file(p.path, p.pattern, p.ignore_case, p.search_only_values, p.exclude_keys, p.include_keys)

    if (p.command == 'compare'):
        Colorize.print("Comparing \"%s\" -> \"%s\"" % (p.file_a, p.file_b), Colorize.OKCYAN)
        Colorize.print("Legend: ! = different, - = only in first, + = only in second", Colorize.OKCYAN)
        data_a = parse_string_table_file(p.file_a)
        data_b = parse_string_table_file(p.file_b)
        compare(data_a, data_b)

    if (p.command == 'compare-git'):
        if os.path.isdir(p.file):
            for file in os.listdir(p.file):
                if file.endswith(".uasset"):
                    file_path = os.path.join(p.file, file)
                    Colorize.print("Comparing \"%s\" in \"%s\" (revision A = \"%s\", revision B = \"%s\")" % (file_path, p.work_dir, p.revision_a, p.revision_b), Colorize.OKCYAN)
                    Colorize.print("Legend: ! = different, - = only in first, + = only in second", Colorize.OKCYAN)
                    compare_inside_git_repo(p.work_dir, file_path, p.revision_a, p.revision_b)
        else:
            Colorize.print("Comparing \"%s\" in \"%s\" (revision A = \"%s\", revision B = \"%s\")" % (p.file, p.work_dir, p.revision_a, p.revision_b), Colorize.OKCYAN)
            Colorize.print("Legend: ! = different, - = only in first, + = only in second", Colorize.OKCYAN)
            compare_inside_git_repo(p.work_dir, p.file, p.revision_a, p.revision_b)

    if (p.command == 'meta'):
        Colorize.print("Getting meta for \"%s\"" % p.file, Colorize.OKCYAN)
        data = open(p.file, mode='rb').read()
        meta = parse_meta(data)
        Colorize.print("File version:    %d" % meta.file_version)
        Colorize.print("License version: %d" % meta.license_version)
        Colorize.print("UE version:      %d" % meta.ue_version)
        Colorize.print("Cook version:    %d" % meta.cook_version)
        Colorize.print("Asset type:      %d" % meta.asset_type)
        Colorize.print("Data offset:     %d" % meta.data_offset)
        Colorize.print("Folder path:     %s" % meta.path)
        Colorize.print("Asset GUID:      %s" % meta.asset_guid)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
        sys.exit(0)
    except Exception as e:
        sys.stderr.write("Error: %s" % e)
        sys.exit(1)