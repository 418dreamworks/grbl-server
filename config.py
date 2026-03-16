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
SPINDLE_WARMUP = 5

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

# ============================================================
# FEED RATE LOOKUP (for drilling)
# ============================================================

# Feed rate lookup table (tool_dia_mm: feed_mm_per_min)
# Interpolates between points, clamps at extremes
FEED_TABLE = {
    0.5: 100,
    1.0: 150,
    3.0: 200,
    6.0: 300,
}

def feed_for_tool(tool_dia):
    """Get feed rate for tool diameter with interpolation."""
    points = sorted(FEED_TABLE.items())
    # Clamp to extremes
    if tool_dia <= points[0][0]:
        return points[0][1]
    if tool_dia >= points[-1][0]:
        return points[-1][1]
    # Interpolate
    for i in range(len(points) - 1):
        d1, f1 = points[i]
        d2, f2 = points[i + 1]
        if d1 <= tool_dia <= d2:
            t = (tool_dia - d1) / (d2 - d1)
            return f1 + t * (f2 - f1)
    return points[-1][1]

# ============================================================
# GRBL SETTINGS BACKUP (2026-02-03)
# ============================================================
# $0=6        # Step pulse time (microseconds)
# $1=255      # Step idle delay (milliseconds)
# $2=15       # Step port invert mask
# $3=4        # Direction port invert mask
# $4=0        # Step enable invert
# $5=0        # Limit pins invert
# $6=0        # Probe pin invert
# $10=255     # Status report mask
# $11=0.010   # Junction deviation (mm)
# $12=0.010   # Arc tolerance (mm)
# $13=0       # Report inches (0=mm)
# $20=0       # Soft limits enable
# $21=0       # Hard limits enable
# $22=1       # Homing cycle enable
# $23=0       # Homing direction invert mask
# $24=50.000  # Homing feed rate (mm/min)
# $25=1500.000 # Homing seek rate (mm/min)
# $26=250     # Homing debounce (ms)
# $27=2.000   # Homing pull-off (mm)
# $30=24000   # Max spindle speed (RPM)
# $31=0       # Min spindle speed (RPM)
# $32=0       # Laser mode
# $33=0       # Spindle PWM freq
# $34=0       # Spindle off value
# $35=0       # Spindle min value
# $36=0       # Spindle max value
# $37=0       # Stepper deenergize mask
# $38=10      # Spindle encoder PPR
# $39=1       # Stepper enable off delay
# $100=160.000  # X steps/mm
# $101=161.430  # Y steps/mm
# $102=161.450  # Z steps/mm
# $103=71.111   # A steps/deg
# $110=3000.000 # X max rate (mm/min)
# $111=3000.000 # Y max rate (mm/min)
# $112=1500.000 # Z max rate (mm/min)
# $113=4500.000 # A max rate (deg/min)
# $120=300.000  # X acceleration (mm/sec^2)
# $121=300.000  # Y acceleration (mm/sec^2)
# $122=200.000  # Z acceleration (mm/sec^2)
# $123=20.000   # A acceleration (deg/sec^2)
# $130=830.000  # X max travel (mm)
# $131=420.000  # Y max travel (mm)
# $132=80.000   # Z max travel (mm)
# $133=1000000.000 # A max travel (deg)
