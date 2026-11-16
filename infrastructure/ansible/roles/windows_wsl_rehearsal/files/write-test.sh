#!/bin/sh
# The write test of the rehearsal (role windows_wsl_rehearsal), as root in the
# distro of the temporary account: one try to make one new file, with one fixed
# name, in the textfile directory of the host ($1), through the automount of
# drvfs. A file that the try made is removed at once, and the play removes it
# too. It prints one line:
#
#   refused <errno> <message>   Windows refused the try (EACCES or EPERM)
#   written                     the try made a file (removed)
#   other <errno> <message>     another answer, such as ENOENT, EEXIST or EIO
#
# The errno comes from the message of busybox, which is strerror() of musl.
dir="$1"
file="$dir/tnwks-rehearsal-write-test.tmp"
err=/tmp/tnwks-write-test.err
if ! ls "$dir" >/dev/null 2>"$err"; then
    echo "other NOT_LISTED $(cat "$err")"
    exit 0
fi
# set -C: the shell opens the file with O_EXCL, so it never opens a file that is there.
if (set -C; : >"$file") 2>"$err"; then
    rm -f "$file"
    echo "written"
    exit 0
fi
message=$(cat "$err")
case "$message" in
    *"Permission denied"*) echo "refused EACCES $message" ;;
    *"Operation not permitted"*) echo "refused EPERM $message" ;;
    *"No such file or directory"*) echo "other ENOENT $message" ;;
    *"File exists"*) echo "other EEXIST $message" ;;
    *"I/O error"*) echo "other EIO $message" ;;
    *) echo "other UNKNOWN $message" ;;
esac
exit 0
