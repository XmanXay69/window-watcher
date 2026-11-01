#!/bin/zsh
# Makes a "Window Watcher Signing" certificate for signing the app, once.
#
# Why: macOS remembers Screen Recording permission per app signature. With the
# same certificate every time, the permission survives updates. Without one,
# every new build counts as a new app and has to be allowed again.
#
# The certificate holds no personal info. It lives in its own small keychain
# file (not your login keychain) with a random password saved next to it, so
# building never asks for your password. None of it goes into the git repo.
# build_dmg.sh runs this automatically; running it again does nothing.
set -e

DIR="$HOME/.window-watcher-signing"
KEYCHAIN="$DIR/signing.keychain-db"
[ -f "$KEYCHAIN" ] && exit 0

echo "Creating the Window Watcher signing certificate (one time)..."
mkdir -p "$DIR"
chmod 700 "$DIR"
PASSWORD=$(openssl rand -hex 24)
echo "$PASSWORD" > "$DIR/password"
chmod 600 "$DIR/password"

# Remember the keychain search list, because create-keychain adds to it.
ORIGINAL_LIST=("${(@f)$(security list-keychains -d user | sed 's/^ *"//; s/"$//')}")
security create-keychain -p "$PASSWORD" "$KEYCHAIN"
security list-keychains -d user -s "${ORIGINAL_LIST[@]}"
security set-keychain-settings "$KEYCHAIN"  # don't lock itself after a while

# A self-made certificate that's only allowed to sign code, valid 20 years.
TEMP=$(mktemp -d)
cat > "$TEMP/cert.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = Window Watcher Signing
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
openssl req -x509 -newkey rsa:2048 -nodes -days 7300 -config "$TEMP/cert.cnf" \
    -keyout "$TEMP/key.pem" -out "$TEMP/cert.pem" 2>/dev/null
# macOS can only import the older .p12 format, hence the SHA1/3DES options.
P12_PASSWORD=$(openssl rand -hex 16)
openssl pkcs12 -export -inkey "$TEMP/key.pem" -in "$TEMP/cert.pem" -name "Window Watcher Signing" \
    -out "$TEMP/cert.p12" -passout "pass:$P12_PASSWORD" \
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1
security import "$TEMP/cert.p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign
# Let codesign use the key without a password prompt.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$PASSWORD" "$KEYCHAIN" >/dev/null
rm -rf "$TEMP"
echo "Signing certificate ready in $DIR"
