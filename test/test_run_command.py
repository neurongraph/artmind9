"""utils.functions.run_command: argv-list form (vault git transport, phase 1)."""
import sys

from utils.functions import run_command


def test_argv_list_passes_arguments_verbatim(tmp_path):
    # A double quote and a space in one argument: the f-string + shlex.split
    # form cannot express this without escaping; an argv list needs none.
    arg = 'he said "hi" there'
    rc, out, _ = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", arg])
    assert rc == 0
    assert out.strip() == arg


def test_string_form_still_works():
    rc, out, _ = run_command(f'{sys.executable} -c "print(42)"')
    assert rc == 0
    assert out.strip() == "42"


def test_argv_list_honours_cwd(tmp_path):
    rc, out, _ = run_command([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert rc == 0
    assert out.strip() == str(tmp_path.resolve())


def test_argv_list_accepts_a_path_element(tmp_path):
    # A non-str argv element (e.g. a pathlib.Path) is a plausible shape for
    # later git-call-site migrations. subprocess.run accepts PathLike
    # elements directly, but the debug-log line also shlex.joins the argv --
    # this locks in that it coerces to str first instead of raising.
    script = tmp_path / "script.py"
    script.write_text("import sys; print(sys.argv[1])")
    rc, out, _ = run_command([sys.executable, script, "hello"])
    assert rc == 0
    assert out.strip() == "hello"
