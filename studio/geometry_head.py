"""Original parametric head form. All physical coordinates are millimetres."""
import math

SEGMENTS = 96
BROW = 0.64
# Height fraction, half width and half depth as fractions of the brow ellipse.
PROFILE = ((0, .48, .54), (.08, .62, .67), (.22, .78, .79), (.38, .89, .91),
           (.50, .95, .97), (BROW, 1., 1.), (.73, .985, .978),
           (.82, .91, .89), (.90, .74, .73), (.96, .48, .48), (1., .035, .035))


def number(params, name, default, low, high):
    value = params.get(name)
    try:
        value = float(default if value is None or value == '' else value)
    except (ValueError, TypeError):
        raise ValueError(f'{name} 需要数值') from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{name} 需介于 {low:g} 和 {high:g} 之间')
    return value


def ellipse_perimeter(width, length):
    points = [(width * .5 * math.cos(i * math.tau / SEGMENTS),
               length * .5 * math.sin(i * math.tau / SEGMENTS)) for i in range(SEGMENTS)]
    return sum(math.dist(points[i - 1], points[i]) for i in range(SEGMENTS))


def measurements(params):
    circumference = 10 * number(params, 'circumference', 58, 35, 80)
    size = circumference / ellipse_perimeter(160, 196)
    width = number(params, 'width', 160 * size, 85, 265)
    length = number(params, 'length', 196 * size, 105, 310)
    height = number(params, 'height', 218 * size, 115, 350)
    measured = ellipse_perimeter(width, length)
    return dict(width=width, length=length, height=height,
                brow_perimeter_mm=measured, requested_mm=circumference,
                clearance=number(params, 'clearance', 4, .5, 30))


def suggested_pose(dimensions_mm, dims, structure="full_shell"):
    width, length, height = dimensions_mm
    c = dims['clearance']
    scale = max((dims['width'] + 2*c + 6)/width, (dims['length'] + 2*c + 6)/length,
                (dims['height'] + c + 10)/height)
    return dict(scale=round(max(.1, min(6, scale)), 4), rx=0, ry=0, rz=0,
                dx=0, dy=-round(dims['length'] * .16, 1) if structure == 'cap_mask' else 0, dz=-6)


def head_profiles(dims):
    width, length, height = (dims[k] for k in ('width', 'length', 'height'))
    # Interpolate at ~2 mm intervals, including the exact measurement plane.
    levels = sorted(set([i / 100 for i in range(101)] + [p[0] for p in PROFILE]))
    head = []
    for z in levels:
        a, b = next((a, b) for a, b in zip(PROFILE, PROFILE[1:]) if a[0] <= z <= b[0])
        t = (z - a[0]) / (b[0] - a[0])
        wx = width * .5 * (a[1] + t * (b[1] - a[1]))
        ly = length * .5 * (a[2] + t * (b[2] - a[2]))
        ring = []
        for i in range(SEGMENTS):
            angle = math.tau * i / SEGMENTS
            # Polar ellipse keeps each vertex on a fixed azimuth. Ear and nose
            # relief die away before the calibrated brow section.
            cs, sn = math.cos(angle), math.sin(angle)
            radius = 1 / math.hypot(cs / wx, sn / ly)
            ear = max(0., 1 - ((z - .44) / .15) ** 2) ** 2
            nose = max(0., 1 - ((z - .40) / .19) ** 2) ** 2
            radius += width * .037 * ear * abs(cs) ** 24
            radius += length * .045 * nose * max(0., -sn) ** 36
            ring.append(radius)
        head.append((z * height, ring))
    # Recalibrate fractional angular tessellation to the physical tape measure.
    brow = next(row for z, row in head if abs(z - BROW * height) < 1e-6)
    points = [(r * math.cos(i * math.tau / SEGMENTS),
               r * math.sin(i * math.tau / SEGMENTS)) for i, r in enumerate(brow)]
    actual = sum(math.dist(points[i - 1], points[i]) for i in range(SEGMENTS))
    factor = dims['brow_perimeter_mm'] / actual
    head = [(z, [r * factor for r in row]) for z, row in head]
    # At elevation z a head moving vertically upward occupies every higher
    # head section on its way in. The running maximum cuts that entire path.
    envelope = [0.] * SEGMENTS
    clearance = dims['clearance']
    cutter = []
    for z, row in reversed(head):
        envelope = [max(v, r + clearance) for v, r in zip(envelope, row)]
        cutter.append((z + clearance, envelope))
    cutter.reverse()
    return head, cutter


def surface_data(rows):
    verts = [(r * math.cos(i * math.tau / SEGMENTS), r * math.sin(i * math.tau / SEGMENTS), z)
             for z, row in rows for i, r in enumerate(row)]
    faces = []
    for j in range(len(rows) - 1):
        a, b = j * SEGMENTS, (j + 1) * SEGMENTS
        for i in range(SEGMENTS):
            n = (i + 1) % SEGMENTS
            faces.append((a + i, a + n, b + n, b + i))
    faces.append(tuple(reversed(range(SEGMENTS))))
    faces.append(tuple(range(len(verts) - SEGMENTS, len(verts))))
    return verts, faces
