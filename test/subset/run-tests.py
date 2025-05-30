#!/usr/bin/env python3

# Runs a subsetting test suite. Compares the results of subsetting via harfbuzz
# to subsetting via fonttools.

from difflib import unified_diff
import os
import re
import subprocess
import sys
import tempfile
import shutil
import io

from subset_test_suite import SubsetTestSuite

try:
    from fontTools.ttLib import TTFont
except ImportError:
    TTFont = None

ots_sanitize = shutil.which("ots-sanitize")


def subset_cmd(command):
    global hb_subset, subset_process

    # (Re)start shaper if it is dead
    if subset_process.poll() is not None:
        subset_process.stdin.close()
        subset_process.stdout.close()
        subset_process = open_subset_batch_process()

    print("# " + hb_subset + " " + " ".join(command))
    subset_process.stdin.write((";".join(command) + "\n").encode("utf-8"))
    subset_process.stdin.flush()
    return subset_process.stdout.readline().decode("utf-8").strip()


def cmd(command):
    p = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
    )
    (stdoutdata, stderrdata) = p.communicate()
    if stderrdata:
        print(stderrdata, file=sys.stderr)
    return stdoutdata, p.returncode


def fail_test(test, cli_args, message):
    global fails, number
    fails += 1

    expected_file = os.path.join(
        test_suite.get_output_directory(), test.get_font_name()
    )

    print("not ok %d - %s" % (number, test))
    print("   ---", file=sys.stderr)
    print('   message: "%s"' % message, file=sys.stderr)
    print('   test.font_path: "%s"' % os.path.abspath(test.font_path), file=sys.stderr)
    print(
        '   test.profile_path: "%s"' % os.path.abspath(test.profile_path),
        file=sys.stderr,
    )
    print('   test.unicodes: "%s"' % test.unicodes(), file=sys.stderr)
    print('   expected_file: "%s"' % os.path.abspath(expected_file), file=sys.stderr)
    print("   ...", file=sys.stderr)
    return False


def run_test(test, should_check_ots, preprocess):
    global number
    number += 1

    out_file = os.path.join(
        out_dir, test.get_font_name() + "-subset" + test.get_font_extension()
    )
    cli_args = [
        "--font-file=" + test.font_path,
        "--output-file=" + out_file,
        "--unicodes=%s" % test.unicodes(),
        "--drop-tables+=DSIG,BASE",
        "--drop-tables-=sbix",
    ]
    if preprocess:
        cli_args.extend(
            [
                "--preprocess",
            ]
        )

    cli_args.extend(test.get_profile_flags())
    if test.get_instance_flags():
        cli_args.extend(["--instance=%s" % ",".join(test.get_instance_flags())])
    if test.iup_optimize:
        cli_args.extend(
            [
                "--optimize",
            ]
        )
    ret = subset_cmd(cli_args)

    if ret != "success":
        return fail_test(test, cli_args, "%s failed" % " ".join(cli_args))

    expected_file = os.path.join(
        test_suite.get_output_directory(), test.get_font_name()
    )
    with open(expected_file, "rb") as fp:
        expected_contents = fp.read()
    with open(out_file, "rb") as fp:
        actual_contents = fp.read()

    if expected_contents == actual_contents:
        if should_check_ots:
            print("# Checking output with ots-sanitize.")
            if not check_ots(out_file):
                return fail_test(test, cli_args, "ots for subsetted file fails.")
        return True

    if TTFont is None:
        print("# fonttools is not present, skipping TTX diff.")
        return fail_test(test, cli_args, "hash for expected and actual does not match.")

    with io.StringIO() as fp:
        try:
            with TTFont(expected_file) as font:
                font.saveXML(fp)
        except Exception as e:
            print("#", e)
            return fail_test(test, cli_args, "ttx failed to parse the expected result")
        expected_ttx = fp.getvalue()

    with io.StringIO() as fp:
        try:
            with TTFont(out_file) as font:
                font.saveXML(fp)
        except Exception as e:
            print("#", e)
            return fail_test(test, cli_args, "ttx failed to parse the actual result")
        actual_ttx = fp.getvalue()

    if actual_ttx != expected_ttx:
        for line in unified_diff(expected_ttx.splitlines(1), actual_ttx.splitlines(1)):
            sys.stdout.write(line)
        sys.stdout.flush()
        return fail_test(test, cli_args, "ttx for expected and actual does not match.")

    return fail_test(
        test,
        cli_args,
        "hash for expected and actual does not match, "
        "but the ttx matches. Expected file needs to be updated?",
    )


def has_ots():
    if not ots_sanitize:
        print("# OTS is not present, skipping all ots checks.")
        return False
    return True


def check_ots(path):
    ots_report, returncode = cmd([ots_sanitize, path])
    if returncode:
        print("# OTS Failure: %s" % ots_report)
        return False
    return True


args = sys.argv[1:]
if not args or sys.argv[1].find("hb-subset") == -1 or not os.path.exists(sys.argv[1]):
    sys.exit("First argument does not seem to point to usable hb-subset.")
hb_subset, args = args[0], args[1:]

if not len(args):
    sys.exit("# No tests supplied.")

print("TAP version 14")

has_ots = has_ots()

env = os.environ.copy()
env["LC_ALL"] = "C"

EXE_WRAPPER = os.environ.get("MESON_EXE_WRAPPER")


def open_subset_batch_process():
    cmd = [hb_subset, "--batch"]
    if EXE_WRAPPER:
        cmd = [EXE_WRAPPER] + cmd

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stdout,
        env=env,
    )
    return process


subset_process = open_subset_batch_process()
out_dir = tempfile.mkdtemp()

number = 0
fails = 0
for path in args:
    with open(path, mode="r", encoding="utf-8") as f:
        print("# Running tests in " + path)
        test_suite = SubsetTestSuite(path, f.read())
        for test in test_suite.tests():
            # Tests are run with and without preprocessing, results should be the
            # same between them.
            for preprocess in [False, True]:
                if run_test(test, has_ots, preprocess):
                    print("ok %d - %s" % (number, test))

if fails != 0:
    print("# %d test(s) failed; output left in %s" % (fails, out_dir))
else:
    print("# All tests passed.")
    shutil.rmtree(out_dir)

print("1..%d" % number)
