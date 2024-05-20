#!/usr/bin/python

import os
import sys

import pickle


if __name__ == '__main__':
    if os.geteuid():
        sys.exit('ERROR: expect root')

    try:
        if (num := int.from_bytes(os.read(0, 4), 'little')) < 0 or num > 4096:
            raise Exception;
        obj = pickle.loads(os.read(0, num))
    except Exception:
        sys.exit('ERROR: invalid argument')

    obj.priv()