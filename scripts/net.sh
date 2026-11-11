#!/bin/sh

# Fetch the default Atomic NNUE network into the current directory (src/).
#
# The Makefile's `net` target runs this with the network's identity in the
# environment, so the file name, the release URL and the SHA-256 live in one
# place (src/Makefile, checked against EvalFileDefaultName in evaluate.h):
#
#   NET_NAME    canonical file name (EvalFileDefaultName)
#   NET_URL     published release asset
#   NET_SHA256  expected SHA-256 of the file, lower-case hex
#   EVALFILE    optional: a network supplied by the caller (OpenBench). It is
#               copied under NET_NAME as it is, never checked against NET_SHA256
#               (OpenBench authenticates it by its own SHA) and nothing is
#               downloaded.
#
# A file under NET_NAME is only ever replaced by a complete, verified one: the
# download goes to a temporary file that is renamed into place once its
# SHA-256 matches. An existing file that does not match is removed and fetched
# again.
#
# With "0" as the first argument (universal builds whose compiler lacks
# --embed-dir) the network is also dumped as a C string literal.

: "${NET_NAME:?NET_NAME is not set; run this script through 'make net'}"
: "${NET_URL:?NET_URL is not set; run this script through 'make net'}"
: "${NET_SHA256:?NET_SHA256 is not set; run this script through 'make net'}"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d ' ' -f 1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | sed 's/^.*= *//'
  else
    return 1
  fi
}

# 0: valid, 1: wrong contents, 2: no SHA-256 tool
validate_network() {
  _actual=$(sha256 "$1") || return 2
  [ "$(printf '%s' "$_actual" | tr 'A-F' 'a-f')" = "$NET_SHA256" ]
}

download() {
  # $1 = URL, $2 = output file. Time out so a stalled server fails the build
  # instead of hanging it.
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 30 --max-time 600 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --timeout=60 --tries=1 -O "$2" "$1"
  else
    >&2 printf '%s\n' "Neither curl nor wget is installed." \
      "Install one of them, or download $NET_URL" \
      "into src/ yourself (SHA-256 $NET_SHA256)."
    exit 1
  fi
}

dump_network() {
  if [ "$1" = "0" ]; then
    DUMP_FILE=universal/network_dump.inc
    printf '"' > "$DUMP_FILE"
    hexdump -v -e '"\\" "x" 1/1 "%02X"' "$NET_NAME" >> "$DUMP_FILE"
    printf '"' >> "$DUMP_FILE"
  fi
}

if [ -n "$EVALFILE" ]; then
  if [ ! -f "$EVALFILE" ]; then
    >&2 echo "EVALFILE does not exist: $EVALFILE"
    exit 1
  fi
  if [ "$EVALFILE" != "$NET_NAME" ] && [ "$EVALFILE" != "./$NET_NAME" ]; then
    cp "$EVALFILE" "$NET_NAME.tmp" && mv "$NET_NAME.tmp" "$NET_NAME" || {
      rm -f "$NET_NAME.tmp"
      >&2 echo "Could not copy $EVALFILE to $NET_NAME"
      exit 1
    }
  fi
  echo "Using EVALFILE=$EVALFILE as $NET_NAME, no download"
  dump_network "$1"
  exit 0
fi

if ! sha256 /dev/null >/dev/null 2>&1; then
  >&2 echo "No SHA-256 tool (sha256sum, shasum or openssl) found; cannot verify $NET_NAME."
  exit 1
fi

if [ -f "$NET_NAME" ]; then
  if validate_network "$NET_NAME"; then
    echo "Existing $NET_NAME validated, skipping download"
    dump_network "$1"
    exit 0
  fi
  echo "Removing $NET_NAME: its SHA-256 is not $NET_SHA256"
  rm -f "$NET_NAME"
fi

tmp="$NET_NAME.download.$$"
trap 'rm -f "$tmp"' EXIT
trap 'rm -f "$tmp"; exit 1' INT TERM

attempt=1
while [ "$attempt" -le 3 ]; do
  echo "Downloading $NET_URL (attempt $attempt of 3) ..."
  rm -f "$tmp"
  if download "$NET_URL" "$tmp"; then
    if validate_network "$tmp"; then
      mv "$tmp" "$NET_NAME"
      echo "Successfully validated $NET_NAME"
      dump_network "$1"
      exit 0
    fi
    echo "Downloaded file does not match SHA-256 $NET_SHA256, discarded"
  else
    echo "Download failed"
  fi
  attempt=$((attempt + 1))
  [ "$attempt" -le 3 ] && sleep 5
done

>&2 printf '%s\n' "Failed to download $NET_NAME." \
  "Download $NET_URL into src/ yourself (SHA-256 $NET_SHA256)," \
  "or build without the network with embed=no."
exit 1
