"""`python -m artmind`: the same entry point as the `artmind` console script.

`artmind vault new` runs its child commands this way (vault_new._artmind), so
they use the interpreter -- and therefore the install -- of the parent process
rather than whichever `artmind` happens to be first on PATH.
"""
from artmind._entry import main

if __name__ == "__main__":
    main()
