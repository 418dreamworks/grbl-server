# NED Jr Configuration
# Edit these values to tune machining parameters

# ============================================================
# TOOL GEOMETRY RATIOS (multiplied by tool diameter)
# ============================================================

# Depth of cut per pass = tool_dia * DOC_RATIO
DOC_RATIO = 0.5  # 50% of tool diameter

# Helical pitch per revolution = tool_dia * PITCH_RATIO
PITCH_RATIO = 1/6  # ~16.7% of tool diameter (3 spirals per DOC)

# Horizontal stepover = tool_dia * STEPOVER_RATIO
STEPOVER_RATIO = 0.25  # 25% of tool diameter

# Helix starting radius (for boring) = HELIX_START_RADIUS mm
HELIX_START_RADIUS = 0.05  # Creates hole = tool_dia + 0.1mm

# Finish stock to leave on non-final passes
FINISH_STOCK = 0.05  # mm

# ============================================================
# FEEDS (mm/min)
# ============================================================

# Helical plunge feed rate
FEED_PLUNGE = 300

# Horizontal cutting feed rate
FEED_CUT = 500

# Probe feed rates
FEED_PROBE_FAST = 50
FEED_PROBE_SLOW = 10
FEED_PROBE_FIXTURE = 10  # Slow for alarm-based detection

# ============================================================
# SPINDLE
# ============================================================

# Default spindle speed (RPM)
SPINDLE_RPM = 12000

# Warmup time after spindle start (seconds)
# Allows user to adjust speed override before cutting begins
SPINDLE_WARMUP = 10

# ============================================================
# PROBING
# ============================================================

# Z probe plate thickness (mm above work surface)
Z_PLATE_THICKNESS = 22.0

# XY probe edge offset (mm from work edge to probe edge)
PROBE_EDGE_OFFSET = 7.0

# Max probe distance before giving up (mm)
PROBE_MAX_DISTANCE = 50

# Backoff distance after probe contact (mm)
PROBE_BACKOFF = 5

# ============================================================
# TOOL DIAMETERS (common sizes in mm)
# ============================================================

TOOL_DIA_QUARTER_INCH = 6.35   # 1/4" = 6.35mm
TOOL_DIA_EIGHTH_INCH = 3.175   # 1/8" = 3.175mm
TOOL_DIA_3MM = 3.0
TOOL_DIA_6MM = 6.0

# Default tool diameter
DEFAULT_TOOL_DIA = TOOL_DIA_QUARTER_INCH

# ============================================================
# SAFETY
# ============================================================

# Safe Z height for rapids (machine coordinates)
SAFE_Z_MPOS = -1

# Retract height above work after operations
RETRACT_HEIGHT = 2  # mm above start/surface

# ============================================================
# ROTARY (Chuck/Tailstock)
# ============================================================

# Chuck probe corner offsets (mm from centerline)
CHUCK_X_OFFSET = -50
CHUCK_Y_OFFSET = -20
CHUCK_Z_OFFSET = 26

# Tailstock edge offset from centerline
TAILSTOCK_EDGE_OFFSET = 17.6  # 7 + 21.2/2

# Alignment tolerance for tailstock square check
ALIGNMENT_TOLERANCE = 0.05  # mm
