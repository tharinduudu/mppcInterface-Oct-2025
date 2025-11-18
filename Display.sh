#!/usr/bin/env bash
cd /home/cosmic/mppcinterface-oct-2022/firmware/libraries/slowControl
tail -f $(ls -t | head -n 1)
