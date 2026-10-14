"""Stand-in for the private module SciPy removed.

dlnpyutils (and therefore thedoppler and fraunhofer) still imports
scipy._lib.array_api_compat. The public array-api-compat package provides
the same names.
"""
from array_api_compat import device, is_array_api_obj, size
from array_api_compat import numpy
