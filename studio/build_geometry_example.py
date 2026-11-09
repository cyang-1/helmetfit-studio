"""Build our original '弦月勘探盔' example. Execute with the bundled Blender."""
import math
from bisect import bisect_right
import sys
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from geometry_head import SEGMENTS, head_profiles, measurements, surface_data
from geometry_mesh import mesh_from, select


def smoothstep(low, high, n):
    t = min(1., max(0., (n-low)/(high-low)))
    return t*t*(3.-2.*t)


def make_example(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    dims = measurements({})
    height = dims['height']
    _, envelope = head_profiles(dims)
    envelope = [(-6., envelope[0][1])] + envelope
    levels = [z for z,_ in envelope]
    wall = 2.7
    def contour_at(z, i):
        k = max(0,min(len(levels)-2,bisect_right(levels,z)-1))
        t = max(0,min(1,(z-levels[k])/(levels[k+1]-levels[k])))
        return (1-t)*envelope[k][1][i]+t*envelope[k+1][1][i]
    shaped = []
    for z, ring in envelope + [(levels[-1]+2.,envelope[-1][1]), (levels[-1]+4.,envelope[-1][1])]:
        h = z / height
        radii = []
        for i, r in enumerate(ring):
            a = i * math.tau / SEGMENTS
            side, front = abs(math.cos(a)), -math.sin(a)
            # Recessed visor belt runs in a shallow V from the temples to the nose.
            visor_level = .535 + .095*(1-max(0.,front))
            front_mask = smoothstep(.15,.65,front)
            brow = 4.0*math.exp(-((h-visor_level-.10)/.032)**2)*front_mask
            chin = 3.4*math.exp(-((h-.25-.06*side)/.08)**2)*front_mask
            temple = 4.5*math.exp(-((h-.48)/.18)**4)*side**15
            ridge = 2.8*math.exp(-((h-.81)/.17)**2)*max(0.,math.cos(6*a))**5
            trim = 1.3*math.exp(-((h-.08)/.020)**2)
            # Offset in radial AND axial directions so crown slope retains
            # material while the sides don't become a near solid volume.
            samples = [wall*k/8 for k in range(-8,9) if z+wall*k/8<=levels[-1]]
            outer = max((contour_at(z+dz,i)+math.sqrt(max(0,wall**2-dz**2)) for dz in samples),default=r)
            rear = max(0.,-front)**12 * 4.65 * math.exp(-((h-.635)/.042)**2)
            radii.append(outer + .66*(brow + chin + temple + ridge + trim)+rear)
        shaped.append((z, radii))
    mesh = mesh_from('弦月勘探盔', *surface_data(shaped))
    palette = [('陶瓷灰',(.48,.55,.58,1),.52), ('深海蓝',(.03,.075,.11,1),.60),
               ('石墨面罩',(.025,.04,.054,1),.88), ('琥珀饰条',(.90,.28,.04,1),.48),
               ('钛灰',(.17,.23,.25,1),.70)]
    for name, color, metallic in palette:
        mat=bpy.data.materials.new(name)
        mat.use_nodes=True
        shader=mat.node_tree.nodes.get('Principled BSDF')
        shader.inputs['Base Color'].default_value=color
        shader.inputs['Metallic'].default_value=metallic
        shader.inputs['Roughness'].default_value=.34
        mesh.data.materials.append(mat)
    for p in mesh.data.polygons:
        c=p.center
        a=math.atan2(c.y,c.x)
        h=c.z/height
        side,front=abs(math.cos(a)), -math.sin(a)
        visor=.535+.095*(1-max(0.,front))
        if front>.19 and abs(h-visor)<.073:
            p.material_index=2
        elif front>.2 and .076<h-visor<.10:
            p.material_index=3
        elif side>.962 and .32<h<.65:
            p.material_index=4 if h>.36 else 3
        elif h<.11 or (.16<h<.39 and front>.4):
            p.material_index=1
        elif h>.68 and (math.cos(6*a)>.46 or front<-.52):
            p.material_index=1
        p.use_smooth=True
    mesh.scale=(.001,.001,.001)
    select(mesh)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',use_selection=True, export_animations=False)


if __name__=='__main__':
    make_example(HERE.parent/'static'/'studio-example'/'helmet.glb')
