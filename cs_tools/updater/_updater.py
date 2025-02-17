from __future__ import annotations

from dataclasses import dataclass
import contextlib
import json
import logging
import os
import pathlib
import shutil
import site
import subprocess as sp
import sys
import venv

log = logging.getLogger(__name__)
logging.getLogger("venv").setLevel(logging.ERROR)


class CSToolsVirtualEnvironment:
    """
    Manage the CS Tools virtual environment.
    """

    IS_WINDOWS = sys.platform == "win32"
    IS_MACOSX = sys.platform == "darwin"

    def __init__(self):
        # instance variables
        self.venv_path = self.get_venv_path()
        self.offline_directory = None

        # the parent application directory
        self.app_dir = self.venv_path.parent

        # useful subdirectories
        self.cache_dir = self.venv_path.parent / ".cache"
        self.log_dir = self.venv_path.parent / ".logs"
        self.tmp_dir = self.venv_path.parent / "tmp"

    @property
    def system_exe(self) -> pathlib.Path:
        """Get the system python executable."""
        # SEE: https://virtualenv.pypa.io/en/16.7.9/reference.html#compatibility-with-the-stdlib-venv-module
        base_exe = pathlib.Path(sys._base_executable)

        if not base_exe.exists():
            raise FileNotFoundError(
                f"Global Python ({base_exe}) could not be found, please use the install method found in the CS Tools "
                f"documentation."
            )

        return base_exe

    @property
    def exe(self) -> pathlib.Path:
        """Get the Python executable."""
        directory = "Scripts" if self.IS_WINDOWS else "bin"
        exec_name = "python.exe" if self.IS_WINDOWS else "python"
        return self.venv_path / directory / exec_name

    @property
    def exists(self) -> bool:
        return self.exe.exists()

    def get_venv_path(self) -> pathlib.Path:
        """Resolve to a User configuration-supported virtual environment directory."""
        if self.IS_WINDOWS:
            user_directory = pathlib.Path(os.environ.get("APPDATA", "~"))
        elif self.IS_MACOSX:
            user_directory = pathlib.Path("~/Library/Application Support")
        else:
            user_directory = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", "~/.config"))

        cs_tools_venv_dir = pathlib.Path(user_directory).expanduser() / "cs_tools" / ".cs_tools"

        # BPO-45337 - handle Micrsoft Store downloads
        #   @steve.dower
        #     We *could* limit this to when it's under AppData, but I think limiting it to Windows is enough.
        #     If the realpath generated a different path, we should warn the caller.
        #     If someone is looking at the output they'll get an important hint.
        #
        #   Further reading: https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes
        #
        if self.IS_WINDOWS:
            context = venv.EnvBuilder().ensure_directories(cs_tools_venv_dir)

            if cs_tools_venv_dir.resolve() != pathlib.Path(context.env_dir).resolve():
                log.debug(
                    "Actual environment location may have moved due to redirects, links or junctions."
                    "\n  Requested location: '%s'"
                    "\n  Actual location:    '%s'",
                    cs_tools_venv_dir,
                    context.env_dir,
                )

            cs_tools_venv_dir = pathlib.Path(context.env_dir)

        return cs_tools_venv_dir.resolve()

    @staticmethod
    def run(
        *args, raise_on_failure: bool = True, should_stream_output: bool = True, base_log_level: str = "INFO", **kwargs
    ) -> sp.CompletedProcess:
        """Run a SHELL command."""
        final_stdout: list[str] = []
        final_stderr: list[str] = []

        # DEV NOTE @boonhapus, 2024/02/15
        #   We want to capture and stream the output of the subprocess as it comes in,
        #   so that the user doesn't think it's frozen.
        #
        popen_streaming_options = {
            "stdout": sp.PIPE,
            "stderr": sp.STDOUT,
            "text": True,
            "bufsize": 1,
        }

        with sp.Popen(args, **popen_streaming_options, encoding="utf-8", **kwargs) as proc:  # type: ignore[call-overload]
            for line in iter(proc.stdout.readline, ""):
                if line.startswith(("ERROR", "WARNING")):
                    level, _, line = line.partition(": ")
                    buffer = final_stderr
                else:
                    level = base_log_level
                    buffer = final_stdout

                if should_stream_output and line:
                    log.log(level=getattr(logging, level), msg=line.strip())

                buffer.append(line)

        if raise_on_failure and proc.returncode != 0:
            cmd = " ".join(arg.replace(" ", "") for arg in args)
            raise RuntimeError(f"Failed with exit code: {proc.returncode}\n\nPIP COMMAND BELOW\n{cmd}")

        output_as_bytes = "\n".join(final_stdout).encode()
        errors_as_bytes = "\n".join(final_stderr).encode()
        return sp.CompletedProcess(proc.args, proc.returncode, stdout=output_as_bytes, stderr=errors_as_bytes)

    def is_package_installed(self, package_name: str, with_system_python: bool = False) -> bool:
        """Check if a package is installed. This should be called only within CS Tools."""
        cp = self.pip("list", "--format", "json", should_stream_output=False, with_system_python=with_system_python)

        for installed in json.loads(cp.stdout.decode()):
            if installed["name"] == package_name:
                return True

        return False

    def check_if_globally_installed(self, *, remove: bool = False) -> bool:
        """Check if self is installed in global python, which would PATH-shadow the VENV binary."""
        is_globally_installed = self.is_package_installed("cs_tools", with_system_python=True)
        is_system_is_venv_exe = self.exe.resolve() == self.system_exe.resolve()

        if is_globally_installed and not is_system_is_venv_exe:
            # fmt: off
            log.warning(
                f"CS Tools and many other dependencies were found to be globally installed at {self.system_exe} !!"
                f"\nRemoving CS Tools now.."
                f"\n"
                f"\nIf you did not intend to install the CS Tools globally, it is advisable to run the following "
                f"commands to clean up your environment. "
                f"\nThis will reset the python environment to default."
                f"\n"
                f"\n  1.  python -m pip freeze > global-env-requirements.txt"
                f"\n  2.  python -m pip uninstall -r global-env-requirements.txt --yes"
                f"\n  3.  rm global-env-requirements.txt"
                f"\n"
            )
            # fmt: on

            if remove:
                self.pip("uninstall", "cs_tools", "--yes", with_system_python=True)
                is_globally_installed = False

        return is_globally_installed

    def python(self, *args, **kwargs) -> sp.CompletedProcess:
        """Run a command in the virtual environment."""
        return self.run(self.exe.as_posix(), *args, **kwargs)

    def system_python(self, *args, **kwargs) -> sp.CompletedProcess:
        """Run a command in the global python environment."""
        return self.run(self.system_exe.as_posix(), *args, **kwargs)

    def pip(self, command: str, *args, with_system_python: bool = False, **kwargs) -> sp.CompletedProcess:
        """Run a command in the virtual environment's pip."""
        # fmt: off
        required_general_args = (
            # ignore environment variables and user configuration
            "--isolated",
            # disable caching
            "--no-cache-dir",
            # don't ping pypi for new versions of pip -- it doesn't matter and is noisy
            "--disable-pip-version-check",
            # trust installs from the official python package index and the thoughtspot github repos
            "--trusted-host", "files.pythonhosted.org",
            "--trusted-host", "pypi.org",
            "--trusted-host", "pypi.python.org",
            "--trusted-host", "github.com",
            "--trusted-host", "codeload.github.com",
        )
        # fmt: on

        # TODO: proxy = get from ENVVAR ?
        # required_general_args = (*required_general_args, "--proxy", "scheme://[user:passwd@]proxy.server:port")

        if command == "install" and self.offline_directory:
            # override incoming args
            # fmt: off
            args = (
                "--requirement", self.offline_directory.joinpath("requirements.txt").as_posix(),
                "--upgrade",
                "--upgrade-strategy", "eager",
                "--progress-bar", "off",
                "--find-links", self.offline_directory.joinpath("dependencies").as_posix(),
                "--no-index",
            )
            # fmt: on

        python = self.system_python if with_system_python else self.python

        return python("-m", "pip", command, *required_general_args, *args, **kwargs)

    def with_offline_mode(self, find_links: pathlib.Path) -> None:
        """Set CS Tools into offline mode, fetching requirements from path."""
        self.offline_directory = find_links

    def ensure_directories(self) -> None:
        # ala venv.EnvBuilder.ensure_directories
        self.venv_path.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        # Clean the temporary directory.
        for path in self.tmp_dir.iterdir():
            try:
                path.unlink(missing_ok=True) if path.is_file() else shutil.rmtree(path, ignore_errors=True)
            except PermissionError:
                log.warning(f"{path} appears to be in use and can't be cleaned up.. do you have it open somewhere?")

    def make(self) -> None:
        """Create the virtual environment."""
        if self.exists:
            return

        # TODO: need to replace this with an implementation which finds the system python executable, apparently
        #       sys.base_prefix is NOT the way.
        #
        # LIKELY: https://virtualenv.pypa.io/en/16.7.9/reference.html#compatibility-with-the-stdlib-venv-module
        python = sys.executable

        # Run with global/system python , equivalent to..
        #   python -m venv $USER_DIR/cs_tools/.cs_tools
        log.debug(f"Executing venv creation: {python} -m venv {self.venv_path}")
        self.run(python, "-m", "venv", self.venv_path.as_posix())

        # Ensure `pip` is at least V23.1 so that backjumping is available
        self.python("-m", "ensurepip")
        self.pip("install", "pip >= 23.1", "--upgrade")

    def reset(self) -> None:
        """Reset the virtual environment to base."""
        # Destroy the venv.
        shutil.rmtree(self.venv_path, ignore_errors=True)

        # Re-make the venv.
        self.make()


cs_tools_venv = CSToolsVirtualEnvironment()


@dataclass
class FishPath:
    """
    fish's PATH is managed via the fish CLI.

    Further Reading:
      https://fishshell.com/docs/current/tutorial.html#path
    """

    venv: CSToolsVirtualEnvironment

    @property
    def bin_dir(self) -> pathlib.Path:
        return self.venv.exe.parent

    def add(self) -> None:
        # This is indempotent.
        log.info(f"Adding '{self.bin_dir}' to $fish_user_paths")
        sp.check_output(["fish_add_path", self.bin_dir.as_posix()])

    def unset(self) -> None:
        log.info(f"Removing '{self.bin_dir}' from $fish_user_paths")
        sp.check_output(["set", "PATH", f"(string match -v {self.bin_dir.as_posix()} $PATH)"])


@dataclass
class WindowsPath:
    """
    Window's PATH is best managed on the command line.
    """

    venv: CSToolsVirtualEnvironment

    @property
    def bin_dir(self) -> pathlib.Path:
        return self.venv.exe.parent

    @property
    def sys_pydir(self) -> pathlib.Path:
        return pathlib.Path(site.getuserbase()) / "Scripts"

    def symlink_paths(self, target_path: pathlib.Path, original_path: pathlib.Path) -> None:
        """Attempt to symlink in Windows."""
        target_path.unlink(missing_ok=True)

        try:
            log.info(f"Attempting to symlink: '{target_path}' -> '{original_path}'")
            target_path.symlink_to(original_path)

        # This can happen if the user does not have the correct permissions on Windows
        except OSError:
            log.info("Symlink failed, copying instead..")
            shutil.copy(original_path, target_path)

    def _broadcast(self) -> None:
        import ctypes

        # Tell other processes to update their environment
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x1A
        SMTO_ABORTIFHUNG = 0x0002

        result = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )

    @contextlib.contextmanager
    def win_registry(self):
        import winreg

        # ala   regedit.exe   Computer / HKEY_CURRENT_USER / ENVIRONMENT
        with winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER) as root:
            with winreg.OpenKey(root, "Environment", 0, winreg.KEY_ALL_ACCESS) as key:
                yield key

    def add(self) -> None:
        import winreg

        with self.win_registry() as key:
            PATH, _ = winreg.QueryValueEx(key, "PATH")

            # Couldn't get PATH variable from registry, so we have to try to bruteforce
            # the path linking. First try to symlink, then just copy the damn thing.
            if PATH is None:
                self.symlink_paths(self.sys_pydir / "cs_tools.exe", self.bin_dir / "cs_tools.exe")
                self.symlink_paths(self.sys_pydir / "cstools.exe", self.bin_dir / "cstools.exe")
                return

            # Append to the PATH variable
            if self.bin_dir.as_posix() not in PATH:
                log.info(f"Adding '{self.bin_dir}' to User %PATH%")
                PATH = f"{PATH};{self.bin_dir.as_posix()}"
                winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, PATH)

        self._broadcast()

    def unset(self) -> None:
        import winreg

        with self.win_registry() as key:
            PATH, _ = winreg.QueryValueEx(key, "PATH")

            # Couldn't get the PATH variable from registry
            if PATH is None:
                self.sys_pydir.joinpath("cs_tools.exe").unlink(missing_ok=True)
                self.sys_pydir.joinpath("cstools.exe").unlink(missing_ok=True)
                return

            log.info(f"Removing '{self.bin_dir}' from User %PATH%")
            PATH = PATH.replace(f";{self.bin_dir.as_posix()}", "")
            winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, PATH)

        self._broadcast()


@dataclass
class UnixPath:
    """
    Modify SHELL profiles.

    base profile, zsh, and bash.
    """

    venv: CSToolsVirtualEnvironment

    @property
    def bin_dir(self) -> pathlib.Path:
        return self.venv.exe.parent

    @property
    def home(self) -> pathlib.Path:
        return pathlib.Path("~").expanduser()

    @property
    def profile_snippet(self) -> str:
        addition = (
            # fmt: off
            f"\n# absolute path to ThoughtSpot's CS Tools"
            # append to PATH (instead of prepend) in case of global python environment requiring common packages
            f'\nexport PATH="$PATH:{self.bin_dir}"'
            # If you are dealing with an environment where Python thinks you are restricted to ASCII data.
            # https://click.palletsprojects.com/en/8.1.x/unicode-support/
            f"\nexport LC_ALL=en_US.utf-8"
            f"\nexport LANG=en_US.utf-8"
            f"\n"
            # fmt: on
        )
        return addition

    def get_shell_profiles(self) -> list[pathlib.Path]:
        profiles = []

        # .profile is the base shell profile
        #  - if it doesn't exist, we'll create it
        base_profile = self.home.joinpath(".profile")
        base_profile.touch(exist_ok=True)
        profiles.append(base_profile)

        # .zprofile is for login shells
        # .zshrc is for interactive shells
        if "zsh" in os.environ.get("SHELL", ""):
            zdotdir = pathlib.Path(os.getenv("ZDOTDIR", self.home))

            for profile in [".zprofile", ".zshrc"]:
                profiles.append(zdotdir.joinpath(profile))

        # .bash_profile is for login shells
        # .bashrc is for interactive shells
        for profile in [".bash_profile", ".bashrc"]:
            profiles.append(self.home.joinpath(profile))

        return profiles

    def add(self) -> None:
        for profile in self.get_shell_profiles():
            # don't write to profiles that don't exist
            if not profile.exists():
                continue

            if self.profile_snippet not in profile.read_text():
                log.info(f"Adding the '{self.bin_dir}' PATH snippet to '{profile.resolve()}'")

                with profile.open(mode="a") as f:
                    f.write(self.profile_snippet)

    def unset(self) -> None:
        for profile in self.get_shell_profiles():
            if not profile.exists():
                continue

            contents = profile.read_text()

            if self.profile_snippet in contents:
                log.info(f"Removing the '{self.bin_dir}' PATH snippet from '{profile.resolve()}'")
                contents = contents.replace(self.profile_snippet, "")

                with profile.open(mode="w") as f:
                    f.write(contents)
