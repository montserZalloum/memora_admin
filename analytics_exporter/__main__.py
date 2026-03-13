"""Allow running analytics exporter via: python3 -m analytics_exporter"""

import sys

from .run import main

sys.exit(main())
