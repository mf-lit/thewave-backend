#!/bin/bash
# Encrypt all .env files so they can be safely committed to git

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

for i in .env ; do
	echo "Encrypting $i..."
	sleep 1
	gpg -c --armor $i
	f=$(basename $i)
	sed -i "1s/^/# Decrypt with \`gpg -d $f.asc\`\n/" $i.asc
done


