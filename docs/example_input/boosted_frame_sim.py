"""
This is a typical input script that runs a simulation of
laser-wakefield acceleration using FBPIC.

Usage
-----
- Modify the parameters below to suit your needs
- Type "python -i lpa_sim.py" in a terminal
- When the simulation finishes, the python session will *not* quit.
    Therefore the simulation can be continued by running sim.step()

Help
----
All the structures implemented in FBPIC are internally documented.
Enter "print(fbpic_object.__doc__)" to have access to this documentation,
where fbpic_object is any of the objects or function of FBPIC.
"""

# -------
# Imports
# -------
import numpy as np
from scipy.constants import c
# Import the relevant structures in FBPIC
from fbpic.main import Simulation
from fbpic.lpa_utils.laser import add_laser
from fbpic.lpa_utils.bunch import add_elec_bunch
from fbpic.lpa_utils.boosted_frame import BoostConverter
from fbpic.openpmd_diag import FieldDiagnostic, ParticleDiagnostic, \
                                BoostedFieldDiagnostic
# ----------
# Parameters
# ----------
use_cuda = True

# The simulation box
Nz = 1600        # Number of gridpoints along z
zmax = 0.e-6     # Length of the box along z (meters)
zmin = -80.e-6
Nr = 75          # Number of gridpoints along r
rmax = 150.e-6   # Length of the box along r (meters)
Nm = 2           # Number of modes used
n_guard = 40     # Number of guard cells
exchange_period = 10
# The simulation timestep
dt = (zmax-zmin)/Nz/c   # Timestep (seconds)
N_step = 100     # Number of iterations to perform
                 # (increase this number for a real simulation)

# Boosted frame
gamma_boost = 15.
boost = BoostConverter(gamma_boost)

# The laser (conversion to boosted frame is done inside 'add_laser')
a0 = 2.          # Laser amplitude
w0 = 50.e-6      # Laser waist
ctau = 9.e-6     # Laser duration
z0 = -20.e-6     # Laser centroid
zfoc = 0.e-6     # Focal position
lambda0 = 0.8e-6 # Laser wavelength

# The density profile
w_matched = 50.e-6
ramp_up = 5.e-3
plateau = 8.e-2
ramp_down = 5.e-3

# The particles of the plasma
p_zmin = 0.e-6   # Position of the beginning of the plasma (meters)
p_zmax = ramp_up + plateau + ramp_down
p_rmin = 0.      # Minimal radial position of the plasma (meters)
p_rmax = 100.e-6 # Maximal radial position of the plasma (meters)
n_e = 1.e24      # The density in the labframe (electrons.meters^-3)
p_nz = 2         # Number of particles per cell along z
p_nr = 2         # Number of particles per cell along r
p_nt = 4         # Number of particles per cell along theta
uz_m = 0.        # Initial momentum of the electrons in the lab frame

# Density profile
# Convert parameters to boosted frame
# (NB: the density is converted inside the Simulation object)
ramp_up, plateau, ramp_down = \
    boost.static_length( [ ramp_up, plateau, ramp_down ] )
# Relative change divided by w_matched^2 that allows guiding
rel_delta_n_over_w2 = 1./( np.pi * 2.81e-15 * w_matched**4 * n_e )
# Define the density function
def dens_func( z, r ):
    """
    User-defined function: density profile of the plasma

    It should return the relative density with respect to n_plasma,
    at the position x, y, z (i.e. return a number between 0 and 1)

    Parameters
    ----------
    z, r: 1darrays of floats
        Arrays with one element per macroparticle
    Returns
    -------
    n : 1d array of floats
        Array of relative density, with one element per macroparticles
    """
    # Allocate relative density
    n = np.ones_like(z)
    # Make ramp up
    inv_ramp_up = 1./ramp_up
    n = np.where( z<ramp_up, z*inv_ramp_up, n )
    # Make ramp down
    inv_ramp_down = 1./ramp_down
    n = np.where( (z >= ramp_up+plateau) & (z < ramp_up+plateau+ramp_down),
              - (z - (ramp_up+plateau+ramp_down) )*inv_ramp_down, n )
    # Add transverse guiding parabolic profile
    n = n * ( 1. + rel_delta_n_over_w2 * r**2 )
    return(n)

# The bunch
bunch_zmin = z0 - 27.e-6
bunch_zmax = bunch_zmin + 4.e-6
bunch_rmax = 10.e-6
bunch_gamma = 400.
bunch_n = 5.e23
# Convert parameters to boosted frame
bunch_beta = np.sqrt( 1. - 1./bunch_gamma**2 )
bunch_zmin, bunch_zmax = \
    boost.copropag_length( [ bunch_zmin, bunch_zmax ], beta_object=bunch_beta )
bunch_n, = boost.copropag_density( [bunch_n], beta_object=bunch_beta )
bunch_gamma, = boost.gamma( [bunch_gamma] )

# The moving window (moves with the group velocity in a plasma)
v_window = c*( 1 - 0.5*n_e/1.75e27 )
# Convert parameter to boosted frame
v_window, = boost.velocity( [ v_window ] )

# The diagnostics
diag_period = 100        # Period of the diagnostics in number of timesteps
# Whether to write the fields in the lab frame
Ntot_snapshot_lab = 25
dt_snapshot_lab = (zmax-zmin)/c

# ---------------------------
# Carrying out the simulation
# ---------------------------

# Initialize the simulation object
sim = Simulation( Nz, zmax, Nr, rmax, Nm, dt,
    p_zmin, p_zmax, p_rmin, p_rmax, p_nz, p_nr, p_nt, n_e,
    dens_func=dens_func, zmin=zmin, initialize_ions=True,
#    v_comoving=-0.9999*c, use_galilean=False,
    n_guard=n_guard, exchange_period=exchange_period,
    gamma_boost=gamma_boost, boundaries='open', use_cuda=use_cuda )

# Add an electron bunch
add_elec_bunch( sim, bunch_gamma, bunch_n, bunch_zmin,
                bunch_zmax, 0, bunch_rmax )

# Add a laser to the fields of the simulation
add_laser( sim.fld, a0, w0, ctau, z0, lambda0=lambda0,
           zf=zfoc, gamma_boost=gamma_boost )

# Configure the moving window
sim.set_moving_window( v=v_window, gamma_boost=gamma_boost )

# Add a field diagnostic
sim.diags = [ FieldDiagnostic(diag_period, sim.fld, sim.comm ),
              ParticleDiagnostic(diag_period,
                {"electrons":sim.ptcl[0], "bunch":sim.ptcl[2]}, sim.comm),
              BoostedFieldDiagnostic( zmin, zmax, c,
                dt_snapshot_lab, Ntot_snapshot_lab, gamma_boost,
                period=diag_period, fldobject=sim.fld, comm=sim.comm) ]

### Run the simulation
print('\n Performing %d PIC cycles' % N_step)
sim.step( N_step, use_true_rho=True )
print('')
