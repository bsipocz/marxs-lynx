import copy
from astropy.table import Table
from astropy.utils.data import get_pkg_data_filename
import numpy as np
import transforms3d
from scipy.interpolate import interp1d

from marxs import optics, simulator, design, analysis
from marxs.design.rowland import design_tilted_torus, RowlandTorus
from marxs.math.geometry import Cylinder
from marxs.optics import FlatDetector
from marxs.simulator import Propagator

from . import ralfgrating
from .mirror import MetaShell, MetaShellAperture, metashellgeometry
from .utils import tagversion

filterdata = Table.read(get_pkg_data_filename('data/filtersqe.dat'),
                        format='ascii.ecsv')
filterqe = optics.GlobalEnergyFilter(filterfunc=interp1d(filterdata['energy'] / 1000,
                                                         filterdata['Total_throughput']))
conf = {'inplanescatter': 2e-6,
        'perpplanescatter': 2e-6,
        'blazeang': np.deg2rad(1.6),
        'focallength': 10000.,
        'alphafac': 2.2,
        'betafac': 4.4,
        'grating_size': np.array([20., 50.]),
        'grating_frame': 2.,
        'grating_d': 2e-4,
        'det_kwargs': {'theta': [3.1, 3.18],
                       'd_element': 51.,
                       'elem_class': optics.FlatDetector,
                       'elem_args': {'zoom': [1, 24.576, 12.288],
                                     'pixsize': 0.024}},
    }


def add_rowland_to_conf(conf):
    conf['alpha'] = conf['alphafac'] * conf['blazeang']
    conf['beta'] = conf['betafac'] * conf['blazeang']
    R, r, pos4d = design_tilted_torus(9.6e3, conf['alpha'], conf['beta'])
    conf['rowland'] = RowlandTorus(R, r, pos4d=pos4d)
    conf['blazemat'] = transforms3d.axangles.axangle2mat(np.array([0, 0, 1]),
                                                         -conf['blazeang'])

add_rowland_to_conf(conf)


class LynxGAS(ralfgrating.MeshGrid):
    def __init__(self, conf):
        gg = {'rowland': conf['rowland'],
              'd_element': conf['grating_size'] + 2 * conf['grating_frame'],
              'parallel_spec': np.array([0., 1., 0., 0.]),
              'x_range': [7e3, 1e4],
              'radius': [np.min(metashellgeometry['r_inner']),
                         np.max(metashellgeometry['r_outer'])],
              'normal_spec': np.array([0, 0, 0, 1]),
              'elem_class': optics.CATGrating,
              'elem_args': {'d': conf['grating_d'],
                            'zoom': [1., conf['grating_size'][0] / 2., conf['grating_size'][1] / 2],
                            'orientation':conf['blazemat'],
                            'order_selector': ralfgrating.order_selector_Si}}

        super(LynxGAS, self).__init__(**gg)

        # Color gratings according to the sector they are in
        for e in self.elements:
            e.display = copy.deepcopy(e.display)
            # Angle from baseline in range 0..pi
            ang = np.arctan2(e.pos4d[1, 3], e.pos4d[2, 3]) % (np.pi)
            # pick one colors
            e.display['color'] = 'rgb'[int(ang / np.pi * 3)]


class RowlandDetArray(design.rowland.RowlandCircleArray):
    def __init__(self, conf):
        super(RowlandDetArray, self).__init__(conf['rowland'], **conf['det_kwargs'])


# Place an additional detector on the Rowland circle.
detcirc = optics.CircularDetector(geometry=Cylinder.from_rowland(conf['rowland'],
                                                                 width=50,
                                                                 rotation=np.pi,
                                                                 kwargs={'phi_lim':[-np.pi/2, np.pi/2]}))
detcirc.loc_coos_name = ['detcirc_phi', 'detcirc_y']
detcirc.detpix_name = ['detcircpix_x', 'detcircpix_y']

flatdet = FlatDetector(zoom=[1, 1e5, 1e5])
flatdet.display['shape'] = 'None'


class PerfectLynx(simulator.Sequence):
    '''Default Definition of Lynx without any misalignments'''
    def add_mirror(self, conf):
        return [MetaShellAperture(conf), MetaShell(conf)]

    def add_gas(self, conf):
        return [LynxGAS(conf),
                ralfgrating.catsupport,
                ralfgrating.catsupportbars]

    def add_detectors(self, conf):
        '''Add detectors to the element list

        This is a separate function that is called from __init__ because all
        detectors need different parameters. Placing this specific code in it's own
        function makes it easy to override for derived classes.
        '''
        return [RowlandDetArray(conf),
                analysis.ProjectOntoPlane(),
                Propagator(distance=-1000),
                detcirc,
                Propagator(distance=-1000),
                flatdet,
                FlatDetector(zoom=[1, 50, 50])]

    def post_process(self):
        self.KeepPos = simulator.KeepCol('pos')
        return [self.KeepPos]

    def __init__(self, conf=conf, **kwargs):
        elem = self.add_mirror(conf)
        elem.extend(self.add_gas(conf))
        elem.append(filterqe)
        elem.extend(self.add_detectors(conf))
        elem.append(tagversion)

        super(PerfectLynx, self).__init__(elements=elem,
                                          postprocess_steps=self.post_process(),
                                          **kwargs)
