#!/bin/sh
set -eu
# A small process-isolated example; replace with your own required tests.
printf 'TAP version 13\n1..2\n'
actual="$(/bin/sh solution.sh 2 3)"
if [ "$actual" = "5" ]; then
  printf 'ok 1 - adds positive integers\n'
else
  printf 'not ok 1 - adds positive integers\n'
  exit 1
fi
actual="$(/bin/sh solution.sh -2 -3)"
if [ "$actual" = "-5" ]; then
  printf 'ok 2 - adds negative integers\n'
else
  printf 'not ok 2 - adds negative integers\n'
  exit 1
fi
