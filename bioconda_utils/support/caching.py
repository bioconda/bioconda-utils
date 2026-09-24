"""
Shared on-disk cache for long-running lookups.
"""

import diskcache
import platformdirs

disk_cache = diskcache.Cache(platformdirs.user_cache_dir("bioconda-utils"))
