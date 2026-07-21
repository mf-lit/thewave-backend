#!/bin/bash
# Encrypt all .env files so they can be safely committed to git
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "$SCRIPT_DIR"

read -rs -p "Passphrase: " PASSPHRASE
echo
read -rs -p "Repeat passphrase: " PASSPHRASE_CONFIRM
echo
if [ "$PASSPHRASE" != "$PASSPHRASE_CONFIRM" ]; then
	echo "Passphrases did not match" >&2
	exit 1
fi

for i in .env infra/restic/restic.env ; do
	echo "Encrypting $i..."
	gpg --batch --yes --pinentry-mode loopback --passphrase-fd 3 -c --armor "$i" 3<<< "$PASSPHRASE"
	f=$(basename "$i")
	sed -i "1s/^/# Decrypt with \`gpg -d $f.asc\`\n/" "$i.asc"
done


