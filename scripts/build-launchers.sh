#!/usr/bin/env bash
set -euo pipefail

VERSION="$(
  sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1
)"
RELEASE_TAG="${RELEASE_TAG:-v$VERSION}"
PACKAGE="${TTS_PACKAGE:-tts[kokoro] @ git+https://github.com/CarsonBurke/tts@${RELEASE_TAG}}"
PYTHON_VERSION="${TTS_PYTHON:-3.12}"

mkdir -p release
rm -f \
  release/tts-macos-arm64 \
  release/tts-macos-arm64.sha256 \
  release/tts-linux-x64 \
  release/tts-linux-x64.sha256 \
  release/tts-windows-x64.cmd \
  release/tts-windows-x64.cmd.sha256

write_posix_launcher() {
  local path="$1"
  cat >"$path" <<EOF
#!/usr/bin/env sh
set -eu

default_package='${PACKAGE}'
package="\${TTS_PACKAGE:-\$default_package}"
python_version="\${TTS_PYTHON:-${PYTHON_VERSION}}"
uv_bin="\${UV:-uv}"

if ! command -v "\$uv_bin" >/dev/null 2>&1; then
  echo "tts: uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

state_dir="\${XDG_STATE_HOME:-\$HOME/.local/state}/tts"
stamp="\$state_dir/launcher-package"
tool_bin_stamp="\$state_dir/tool-bin"
expected="\${package}__\${python_version}"
current=""
if [ -r "\$stamp" ]; then
  current="\$(cat "\$stamp")"
fi

tool_bin="\${TTS_TOOL_BIN:-}"
if [ -z "\$tool_bin" ] && [ -r "\$tool_bin_stamp" ]; then
  tool_bin="\$(cat "\$tool_bin_stamp")"
fi
if [ -z "\$tool_bin" ]; then
  tool_bin="\$HOME/.local/share/uv/tools/tts/bin/tts"
fi
if [ ! -x "\$tool_bin" ]; then
  tool_dir="\$("\$uv_bin" tool dir)"
  tool_bin="\$tool_dir/tts/bin/tts"
fi

if [ ! -x "\$tool_bin" ] || [ "\$current" != "\$expected" ]; then
  "\$uv_bin" tool install --python "\$python_version" --reinstall "\$package"
  if [ ! -x "\$tool_bin" ]; then
    tool_dir="\$("\$uv_bin" tool dir)"
    tool_bin="\$tool_dir/tts/bin/tts"
  fi
  if [ ! -x "\$tool_bin" ]; then
    echo "tts: uv installed the tool, but \$tool_bin was not found" >&2
    exit 1
  fi
  mkdir -p "\$state_dir"
  printf '%s' "\$expected" >"\$stamp"
  printf '%s' "\$tool_bin" >"\$tool_bin_stamp"
fi

exec "\$tool_bin" "\$@"
EOF
  chmod +x "$path"
}

write_windows_launcher() {
  local path="$1"
  cat >"$path" <<EOF
@echo off
setlocal

if "%TTS_PACKAGE%"=="" set "TTS_PACKAGE=${PACKAGE}"
if "%TTS_PYTHON%"=="" set "TTS_PYTHON=${PYTHON_VERSION}"
if "%UV%"=="" set "UV=uv"

where "%UV%" >nul 2>nul
if errorlevel 1 (
  echo tts: uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/ 1>&2
  exit /b 1
)

if "%LOCALAPPDATA%"=="" (
  set "STATE_DIR=%USERPROFILE%\.local\state\tts"
) else (
  set "STATE_DIR=%LOCALAPPDATA%\tts"
)
set "STAMP=%STATE_DIR%\launcher-package.txt"
set "TOOL_BIN_STAMP=%STATE_DIR%\tool-bin.txt"
set "EXPECTED=%TTS_PACKAGE%__%TTS_PYTHON%"
set "CURRENT="
if exist "%STAMP%" set /p CURRENT=<"%STAMP%"

if "%TTS_TOOL_BIN%"=="" (
  if exist "%TOOL_BIN_STAMP%" set /p TTS_TOOL_BIN=<"%TOOL_BIN_STAMP%"
)
if "%TTS_TOOL_BIN%"=="" (
  set "TTS_TOOL_BIN=%USERPROFILE%\.local\share\uv\tools\tts\Scripts\tts.exe"
)
if not exist "%TTS_TOOL_BIN%" (
  for /f "delims=" %%D in ('"%UV%" tool dir') do set "TOOL_DIR=%%D"
  set "TTS_TOOL_BIN=%TOOL_DIR%\tts\Scripts\tts.exe"
)

set "NEED_INSTALL="
if not exist "%TTS_TOOL_BIN%" set "NEED_INSTALL=1"
if not "%CURRENT%"=="%EXPECTED%" set "NEED_INSTALL=1"

if defined NEED_INSTALL (
  "%UV%" tool install --python "%TTS_PYTHON%" --reinstall "%TTS_PACKAGE%"
  if errorlevel 1 exit /b %errorlevel%
  if not exist "%TTS_TOOL_BIN%" (
    for /f "delims=" %%D in ('"%UV%" tool dir') do set "TOOL_DIR=%%D"
    set "TTS_TOOL_BIN=%TOOL_DIR%\tts\Scripts\tts.exe"
  )
  if not exist "%TTS_TOOL_BIN%" (
    echo tts: uv installed the tool, but "%TTS_TOOL_BIN%" was not found 1>&2
    exit /b 1
  )
  if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
  >"%STAMP%" echo %EXPECTED%
  >"%TOOL_BIN_STAMP%" echo %TTS_TOOL_BIN%
)

"%TTS_TOOL_BIN%" %*
exit /b %errorlevel%
EOF
}

write_posix_launcher release/tts-macos-arm64
write_posix_launcher release/tts-linux-x64
write_windows_launcher release/tts-windows-x64.cmd

if command -v sha256sum >/dev/null 2>&1; then
  sha256sum release/tts-macos-arm64 >release/tts-macos-arm64.sha256
  sha256sum release/tts-linux-x64 >release/tts-linux-x64.sha256
  sha256sum release/tts-windows-x64.cmd >release/tts-windows-x64.cmd.sha256
else
  shasum -a 256 release/tts-macos-arm64 >release/tts-macos-arm64.sha256
  shasum -a 256 release/tts-linux-x64 >release/tts-linux-x64.sha256
  shasum -a 256 release/tts-windows-x64.cmd >release/tts-windows-x64.cmd.sha256
fi
