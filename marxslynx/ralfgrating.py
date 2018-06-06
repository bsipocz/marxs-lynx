import numpy as np
import astropy.units as u
from scipy.interpolate import RectBivariateSpline
from astropy.utils.data import get_pkg_data_filename as gpdf
from astropy.table import Table
from marxs.base import SimulationSequenceElement
from marxs.optics import GlobalEnergyFilter

from .load_csv import load_table2d, load_number


class InterpolateRalfTable(object):
    '''Order Selector for MARXS using a specific kind of data table.

    The data table was given to me by Ralf. It contains simulated grating
    efficiencies in an Excel table.
    A short summary of this format is given here, to help reading the code.
    The table contains data in 3-dimenasional (wave, n_theta, order) space,
    flattened into a 2d table.

    - Row 1 + 2: Column labels. Not used here.
    - Column A: wavelength in nm.
    - Column B: blaze angle in deg.
    - Rest: data

    For each wavelength there are multiple blaze angles listed, so Column A
    contains
    many dublicates and looks like this: [1,1,1,1,1,1,2,2,2,2,2,2,3,3,3, ...].
    Column B repeats like this: [1,2,3,4,5,6,1,2,3,4,5,6,1,2,3, ...].

    Because the wave, theta grid is regular, this class can use the
    `scipy.interpolate.RectBivariateSpline` for interpolation in each 2-d slice
    (``order`` is an integer and not interpolated).

    Parameters
    ----------
    filename : string
        Location of file with blaze angle and energy dependent grating
        efficiencies.
    k : int
        Degree of spline. See `scipy.interpolate.RectBivariateSpline`.
    '''

    def __init__(self, filename, k=3):
        wave, theta, names, orders = load_table2d(filename)
        theta = theta.to(u.rad)
        # Order is int, we will never interpolate about order,
        # thus, we'll just have
        # len(order) 2d interpolations
        self.orders = np.array([int(n[1:]) for n in names])
        self.interpolators = [RectBivariateSpline(wave, theta, d, kx=k, ky=k) for d in orders]

    def probabilities(self, energies, pol, blaze):
        '''Obtain the probabilties for photons to go into a particular order.

        This has the same parameters as the ``__call__`` method, but it returns
        the raw probabilities, while ``__call__`` will draw from these
        probabilities and assign an order and a total survival probability to
        each photon.

        Parameters
        ----------
        energies : np.array
            Energy for each photons
        pol : np.array
            Polarization for each photon (not used in this class)
        blaze : np.array
            Blaze angle for each photon

        Returns
        -------
        orders : np.array
            Array of orders
        interpprobs : np.array
            This array contains a probability array for each photon to reach a
            particular order
        '''
        # convert energy in keV to wavelength in nm
        # (nm is the unit of the input table)
        wave = (energies * u.keV).to(u.nm, equivalencies=u.spectral()).value
        interpprobs = np.empty((len(self.orders), len(energies)))
        for i, interp in enumerate(self.interpolators):
            interpprobs[i, :] = interp.ev(wave, blaze)
        return self.orders, interpprobs

    def __call__(self, energies, pol, blaze):
        orders, interpprobs = self.probabilities(energies, pol, blaze)
        totalprob = np.sum(interpprobs, axis=0)
        # Cumulative probability for orders, normalized to 1.
        cumprob = np.cumsum(interpprobs, axis=0) / totalprob
        ind_orders = np.argmax(cumprob > np.random.rand(len(energies)), axis=0)

        return orders[ind_orders], totalprob

order_selector_Si = InterpolateRalfTable(gpdf('data/gratings/Si_efficiency.dat',
                                              package='marxslynx'))
order_selector_Si.coating = 'None'
order_selector_SiPt = InterpolateRalfTable(gpdf('data/gratings/SiPt_efficiency.dat',
                                                package='marxslynx'))
order_selector_SiPt.coating = 'Pt'

class RalfQualityFactor(SimulationSequenceElement):
    '''Scale probabilites of theoretical curves to measured values.

    All gratings look better in theory than in practice. This grating quality
    factor scales the calculated diffraction probabilities to the observed
    performance.
    '''

    def __init__(self, **kwargs):
        self.sigma = load_number('gratings', 'debyewaller', 'sigma')
        self.d = load_number('gratings', 'debyewaller', 'd')
        super(RalfQualityFactor, self).__init__(**kwargs)

    def process_photons(self, photons):
        ind = np.isfinite(photons['order'])
        photons['probability'][ind] *= np.exp(- (2 * np.pi * self.sigma / self.d)**2)**(photons['order'][ind]**2)
        return photons


def catsupportbars(photons):
    '''Metal structure that holds grating facets will absorb all photons
    that do not pass through a grating facet.

    We might want to call this L3 support ;-)
    '''
    photons['probability'][photons['facet'] < 0] = 0.
    return photons

# Define L1, L2 blockage as simple filters due to geometric area
# L1 support: blocks 10 % - better than current 18 %
# L2 support: blocks 10 % - better than current 19 %
catsupport = GlobalEnergyFilter(filterfunc=lambda e: 0.9 * 0.9)


def facet_table(gas):
    '''Get table of facet properties

    Parameters
    ----------
    gas : `marxs.simulator.Parallel` inststance
        This can be some kind og container, e.g. a Rowland array

    Returns
    -------
    facettab : `astropy.table.Table`
        Table with facet properties
    '''
    facetpos = np.stack(gas.elem_pos)
    facetang = np.arctan2(facetpos[:, 2, 3], facetpos[:, 1, 3])
    facetrad = np.sqrt(facetpos[:, 1, 3]**2 + facetpos[:, 2, 3]**2)
    facetid = [e.id_num for e in gas.elements]
    facetgrooveang = [e.geometry['groove_angle'] for e in gas.elements]
    facetcoating = [e.order_selector.coating if hasattr(e.order_selector, 'coating') else '--' for e in gas.elements]
    return Table([facetid, facetrad, facetang, facetgrooveang, facetcoating],
                 names=['facet', 'facet_rad', 'facet_ang', 'facet_grooveang',
                        'facet_coating'])
