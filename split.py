#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from absl import app
from absl import flags
import sys
import re
import json
import os
import subprocess
import shutil
import tempfile
from PIL import Image
from pathlib import Path
from natsort import natsorted
from typing import Optional

FLAGS = flags.FLAGS
flags.DEFINE_string("input", None, "The source folder or archive file containing the cue, audio and cover.")
flags.DEFINE_string(
    "cue", None, "The cue file to process. "
    "Will automatically search in --input folder if it's not specified. "
    "Can be absolute path. "
    "Or relative to $PWD if --input is not specified. "
    "Or relative to --input folder or archive root if it's specified")
flags.DEFINE_string("wav", None, "The audio file to process. Search order similar to --cue.")
flags.DEFINE_string(
    "cover", None, "Album cover file, search order similar to --cue. "
    "Set to empty string to disable cover: \"--cover=\".")
flags.DEFINE_string("output", None, "The audio files will be put in \"<output>/<performer>/<album_name>\"")

# metadata
flags.DEFINE_list("cue_encoding", "utf8,gbk,shift-jis", "A list of file encodings to try.")
flags.DEFINE_string("disc_performer_override", None, "Override the performer at disc level.")
flags.DEFINE_bool("embed_cover_art", False, "Embed cover.jpg into every m4a file.")

flags.mark_flag_as_required("output")
flags.DEFINE_alias("i", "input")
flags.DEFINE_alias("o", "output")


def query_yes_no(question, default="yes"):
    """Ask a yes/no question via raw_input() and return their answer.

    "question" is a string that is presented to the user.
    "default" is the presumed answer if the user just hits <Enter>.
        It must be "yes" (the default), "no" or None (meaning
        an answer is required of the user).

    The "answer" return value is True for "yes" or False for "no".
    """
    valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
    if default is None:
        prompt = " [y/n] "
    elif default == "yes":
        prompt = " [Y/n] "
    elif default == "no":
        prompt = " [y/N] "
    else:
        raise ValueError("invalid default answer: '%s'" % default)

    while True:
        sys.stdout.write(question + prompt)
        choice = input().lower()
        if default is not None and choice == '':
            return valid[default]
        elif choice in valid:
            return valid[choice]
        else:
            sys.stdout.write("Please respond with 'yes' or 'no' " "(or 'y' or 'n').\n")


def decompress_archive(fpath: str) -> (tempfile.TemporaryDirectory, Path):
    print("Extracting...", fpath)
    tmp = tempfile.TemporaryDirectory()
    archive_fpath = Path(fpath).resolve()
    if archive_fpath.suffix.lower() in [".rar", ".7z", ".zip"]:
        subprocess.run(["7z", "x", str(archive_fpath), "-o" + tmp.name], check=True)
    else:
        subprocess.run(["tar", "xaf", str(archive_fpath), "-C", tmp.name], check=True)
    d = Path(tmp.name)
    while True:
        inner_files = [x for x in d.iterdir()]
        if len(inner_files) == 1 and inner_files[0].is_dir():
            d = inner_files[0]
            continue
        break
    return tmp, d.resolve()


def determine_cue(rootdir: Optional[Path]) -> Path:
    # Search order:
    # - rootdir + cmdline
    # - pwd + cmdline
    # - rootdir search
    if FLAGS.cue is None:
        # only scan rootdir
        assert rootdir is not None, "Neither --input nor --cue specified."
        cues = [x for x in rootdir.iterdir() if x.is_file() and x.suffix.lower() == ".cue"]
        assert len(cues) >= 1, "No cue found in --input"
        return cues[0].resolve()
    elif rootdir is not None:
        # relpath in rootdir
        assert (rootdir / FLAGS.cue).is_file(), "Invalid --cue file"
        return (rootdir / FLAGS.cue).resolve()
    else:
        # relpath in $PWD
        assert Path(FLAGS.cue).is_file(), "Invalid --cue file"
        return Path(FLAGS.cue).resolve()


def determine_wav(rootdir: Optional[Path], cue_path: Path, cue_dict: dict) -> Path:
    # Search order:
    # - rootdir + cmdline
    # - pwd + cmdline
    # - cuedir + cmdline
    # - rootdir + cue-file
    # - cuedir + cue-file
    # - rootdir search
    # - cuedir search
    if FLAGS.wav is not None:
        # rootdir/$PWD/cue
        if rootdir is not None and (rootdir / FLAGS.wav).is_file():
            return (rootdir / FLAGS.wav).resolve()
        if Path(FLAGS.wav).is_file():
            return Path(FLAGS.wav).resolve()
        if (cue_path.parent / FLAGS.wav).is_file():
            return (cue_path.parent / FLAGS.wav).resolve()
        raise "Invalid --wav file."

    if rootdir is not None:
        f = rootdir / cue_dict["FILE"]
        if f.is_file(): return f.resolve()

    f = cue_path.parent / cue_dict["FILE"]
    if f.is_file(): return f.resolve()

    AUDIO_SUFFIX = ".wav .ape .flac .tta".split(" ")
    if rootdir is not None:
        wavs = [x for x in rootdir.iterdir() if x.is_file() and x.suffix.lower() in AUDIO_SUFFIX]
        if len(wavs) > 1: raise "Unable to determine audio file: " + str(wavs)
        if len(wavs) == 1: return wavs[0].resolve()

    wavs = [x for x in cue_path.parent.iterdir() if x.is_file() and x.suffix.lower() in AUDIO_SUFFIX]
    if len(wavs) > 1: raise "Unable to determine audio file: " + str(wavs)
    if len(wavs) == 1: return wavs[0].resolve()

    raise "Unable to determine audio file: "


def determine_cover(rootdir: Path, cue_path: Path) -> Optional[Path]:
    # Search order:
    # - rootdir + cmdline
    # - pwd + cmdline
    # - cuedir + cmdline
    # - rootdir search
    # - cuedir search
    if FLAGS.cover == '': return None
    if FLAGS.cover is not None:
        if rootdir is not None:
            f = rootdir / FLAGS.cover
            if f.is_file(): return f.resolve()
        if Path(FLAGS.cover).is_file():
            return Path(FLAGS.cover).resolve()
        f = cue_path.parent / FLAGS.cover
        if f.is_file(): return f.resolve()
        raise "Invalid --cover file."

    COVER_SUFFIX = ".jpg .png .tif .tiff".split(" ")
    PRIORITY_COVER_NAMES = [name + ext for name in ["cover", "folder"] for ext in COVER_SUFFIX]
    pics = []

    if rootdir is not None:
        for x in rootdir.iterdir():
            if x.is_file():
                if x.name.lower() in PRIORITY_COVER_NAMES:
                    return x.resolve()
                if x.suffix.lower() in COVER_SUFFIX:
                    pics.append(x)

    if len(pics) == 0:
        for x in cue_path.parent.iterdir():
            if x.is_file():
                if x.name.lower() in PRIORITY_COVER_NAMES:
                    return x.resolve()
                if x.suffix.lower() in COVER_SUFFIX:
                    pics.append(x)

    if len(pics) > 1: pics = natsorted(pics, key=lambda x: x.stem.lower())
    return None if len(pics) == 0 else pics[0].resolve()


def interactive_open_cue(cue_file: Path) -> str:
    with open(cue_file, "rb") as f:
        fbytes = f.read()
    for encoding in FLAGS.cue_encoding:
        try:
            print("Try encoding:", encoding)
            fcontent = fbytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        print("=== CUE content preview ===")
        print(fcontent)
        print()
        ok = query_yes_no("Does this looks correct?")
        if ok:
            # remove BOM
            if fcontent[0] == '\ufeff':
                fcontent = fcontent[1:]
            return fcontent
    raise ValueError("Failed to determine cue file encoding: %s" % str(cue_file))


def parse_cue(cue_content: str) -> dict:
    def unquote(s: str, strip: bool) -> str:
        # s             strip=False     strip=True
        # '"abc " '     'abc '          'abc'
        # '"abc "'      'abc '          'abc'
        # '"abc '       '"abc '         '"abc'
        s_stripped = s.strip()
        if len(s_stripped) >= 2 and s_stripped[0] == '"' and s_stripped[-1] == '"':
            if strip:
                return s_stripped[1:-1].strip()
            else:
                return s_stripped[1:-1]
        else:
            if strip:
                return s_stripped
            else:
                return s

    disc_dict = dict()
    track_list = []

    current_dict = disc_dict
    for line in cue_content.split("\n"):
        line = line.strip()
        if len(line) <= 0:
            continue
        # REM FILE TRACK INDEX
        # track requires TITLE

        # Determines the REGEX
        expect_success = True
        if line[:4] == "REM ":
            p = "^(REM [A-Z_]+) (.+)$"
        elif line[:5] == "FILE ":
            p = "^(FILE) (.+) [A-Z]{3,4}$"
        elif line[:6] == "TRACK ":
            p = "^(TRACK) ([0-9]+) AUDIO$"
        elif line[:6] == "INDEX ":
            p = "^(INDEX) ([0-9]+) ([0-9:]{8,11})$"
        else:
            p = "^([A-Z]+) (.+)$"

        # extract Key-value
        m = re.match(p, line)
        assert not expect_success or m is not None, "Line parse failed: " + line
        key = m[1]
        val = unquote(m[2], strip=True)

        # performer override
        if key == "PERFORMER" and len(track_list) == 0 and FLAGS.disc_performer_override is not None:
            val = FLAGS.disc_performer_override

        if len(val) <= 0:
            print("value is empty, skip line:", line)
            continue

        if key == "INDEX":
            # skip INDEX, it's not used
            continue
        elif key == "TRACK":
            # create a new track item
            track_no = int(val)
            assert track_no == len(track_list) + 1
            track_dict = disc_dict.copy()
            if "TITLE" in track_dict:
                del track_dict["TITLE"]
            current_dict = track_dict
            track_list.append(track_dict)
        elif key == "TITLE":
            current_dict[key] = val
            current_dict["TITLE_RAW"] = unquote(m[2], strip=False)
        else:
            current_dict[key] = val

    # Check that there's a title for each track
    for t in track_list:
        assert "TITLE" in t

    disc_dict["TRACK_LIST"] = track_list
    return disc_dict


def fdkaac_cmd(cue_dict, track_index: int):
    # index starts from 1
    track_dict = cue_dict["TRACK_LIST"][track_index - 1]

    # shnsplit replaces / with -
    # if the title ends with a dot, shnsplit doesn't add an extra dot to deliminate stem and ext.
    fname_stem_in = "{}.{}".format(str(track_index).zfill(2), track_dict["TITLE_RAW"].replace('/', '-'))
    fname_stem_out = "{}.{}".format(str(track_index).zfill(2), track_dict["TITLE"].replace('/', '-'))
    if fname_stem_in[-1] == '.':
        fname_stem_in = fname_stem_in[:-1]
    if fname_stem_out[-1] == '.':
        fname_stem_out = fname_stem_out[:-1]

    ret = ["fdkaac", "--bitrate", "192k", '--gapless-mode', "1", "--moov-before-mdat", "-o", f"{fname_stem_out}.m4a"]
    ret += ["--title", track_dict["TITLE"]]
    ret += ["--artist", track_dict["PERFORMER"]]
    #ret += ["--artist", cue_dict["PERFORMER"]]
    ret += ["--album", cue_dict["TITLE"]]
    if "REM DATE" in cue_dict:
        ret += ["--date", cue_dict["REM DATE"]]
    if "REM GENRE" in cue_dict:
        ret += ["--genre", cue_dict["REM GENRE"]]
    ret += ["--album-artist", cue_dict["PERFORMER"]]
    ret += ["--track", f'{track_index}/{len(cue_dict["TRACK_LIST"])}']

    ret.append(f"wav/{fname_stem_in}.wav")
    return ret


def convert_img(cover: Path, dst: Path):
    # determine image aspect ratio
    img = Image.open(cover)
    print("COVER SIZE =", img.size)
    ratio = img.size[0] / img.size[1]
    if ratio < 0.8:
        do_cut, in_range = False, False
    elif ratio < 1.2:
        do_cut, in_range = False, True
    elif ratio < 1.9:
        do_cut, in_range = False, False
    elif ratio < 2.2:
        do_cut, in_range = True, True
    else:
        do_cut, in_range = True, False
    if not in_range:
        print("Warning: cover aspect ratio not in range", ratio)

    # ^ completely fill (and overflow) the space
    # > only shrink large images
    if not do_cut:
        subprocess.run([
            "convert",
            str(cover), "-colorspace", "RGB", "-filter", "Lanczos2Sharp", "-distort", "Resize", "500x500^>",
            "-colorspace", "sRGB", "-strip", "-interlace", "Plane", "-quality", "90%", "-define",
            "jpeg:dct-method=float",
            str(dst / "cover.jpg")
        ])
    else:
        subprocess.run([
            "convert",
            str(cover), "-colorspace", "RGB", "-gravity", "NorthEast", "-crop", "50%x100%+0+0", "+repage", "-filter",
            "Lanczos2Sharp", "-distort", "Resize", "500x500^>", "-colorspace", "sRGB", "-strip", "-interlace", "Plane",
            "-quality", "90%", "-define", "jpeg:dct-method=float",
            str(dst / "cover.jpg")
        ])


def do_split(wav: Path, cue_dict: dict, cue_content: str, cover: Optional[Path]):
    dst_dir = Path(FLAGS.output).resolve() / cue_dict["PERFORMER"] / cue_dict["TITLE"]
    assert not dst_dir.exists(), str(dst_dir) + " already exists"
    dst_dir.mkdir(parents=True)

    dst_dir_wav = dst_dir / "wav"
    dst_dir_wav.mkdir()

    # split into wav
    os.chdir(dst_dir_wav)
    subprocess.run(["shntool", "split", "-t", '%n.%t', "-o", 'wav', str(wav)], input=cue_content.encode(), check=True)
    # convert each into m4a
    os.chdir(dst_dir)
    m4a_converters = [subprocess.Popen(fdkaac_cmd(cue_dict, x + 1)) for x in range(len(cue_dict["TRACK_LIST"]))]
    for proc in m4a_converters:
        proc.wait()
        assert proc.returncode == 0
    # remove wav
    shutil.rmtree(dst_dir_wav)
    # create cover
    if cover:
        convert_img(cover, dst_dir)
    if FLAGS.embed_cover_art:
        subprocess.run(["bash", '-c', "mp4art --add cover.jpg *.m4a"], check=True)


def main(argv):
    rootdir = None
    decompress_temp = None

    # determin rootdir if --input is specified
    if FLAGS.input is not None:
        p = Path(FLAGS.input)
        assert p.exists(), "Invalid --input option."

        if Path(FLAGS.input).is_file():
            # the input folder is an archive
            decompress_temp, rootdir = decompress_archive(FLAGS.input)
        else:
            rootdir = Path(FLAGS.input).resolve()

    # determine files
    cue = determine_cue(rootdir)
    cue_content = interactive_open_cue(cue)
    cue_dict = parse_cue(cue_content)
    wav = determine_wav(rootdir, cue, cue_dict)
    cover = determine_cover(rootdir, cue)
    print("CUE   file:", cue.name)
    print("AUDIO file:", wav.name)
    print("COVER file:", cover.name if cover else None)
    ok = query_yes_no("Is this OK?")
    if not ok:
        print("Exit...")
        sys.exit()

    # convert
    do_split(wav, cue_dict, cue_content, cover)


if __name__ == '__main__':
    app.run(main)
