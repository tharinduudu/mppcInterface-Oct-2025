#!/usr/bin/env bash
cd /home/cosmic/mppcInterface/firmware/libraries/slowControl
tail -f $(ls -t | head -n 1)
