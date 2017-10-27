# Copyright 2016, FBPIC contributors
# Authors: Remi Lehe, Manuel Kirchen
# License: 3-Clause-BSD-LBNL
"""
This file is part of FBPIC (Fourier-Bessel Particle-In-Cell code).
It defines the class that performs the Hankel transform.

Definition of the Hankel forward and backward transform of order p:
g(\nu) = 2 \pi \int_0^\infty f(r) J_p( 2 \pi \nu r) r dr
f( r ) = 2 \pi \int_0^\infty g(\nu) J_p( 2 \pi \nu r) \nu d\nu d
"""
import numpy as np
from scipy.special import jn, jn_zeros

# Check if CUDA is available, then import CUDA functions
from fbpic.cuda_utils import cuda_installed
if cuda_installed:
    from pyculib import blas as cublas
    from fbpic.cuda_utils import cuda, cuda_tpb_bpg_2d
    from .cuda_methods import cuda_copy_2d_to_2d


class DHT(object):
    """
    Class that allows to perform the Discrete Hankel Transform.
    """

    def __init__(self, p, m, Nr, Nz, rmax, use_cuda=False ):
        """
        Calculate the r (position) and nu (frequency) grid
        on which the transform will operate.

        Also store auxiliary data needed for the transform.

        Parameters:
        ------------
        p: int
        Order of the Hankel transform

        m: int
        The azimuthal mode for which the Hankel transform is calculated

        Nr, Nz: float
        Number of points in the r direction and z direction

        rmax: float
        Edge of the box in which the Hankel transform is taken
        (The function is assumed to be zero at that point.)

        use_cuda: bool, optional
        Whether to use the GPU for the Hankel transform
        """
        # Register whether to use the GPU.
        # If yes, initialize the corresponding cuda object
        self.use_cuda = use_cuda
        if (self.use_cuda==True) and (cuda_installed==False):
            self.use_cuda = False
            print('** Cuda not available for Hankel transform.')
            print('** Performing the Hankel transform on the CPU.')
        if self.use_cuda:
            # Initialize a cuda stream (required by cublas)
            self.blas = cublas.Blas()
            # Initialize two buffer arrays on the GPU
            # The cuBlas API requires that these arrays be in Fortran order
            zero_array = np.zeros((Nz, Nr), dtype=np.complex128, order='F')
            self.d_in = cuda.to_device( zero_array )
            self.d_out = cuda.to_device( zero_array )
            # Initialize the threads per block and block per grid
            self.dim_grid, self.dim_block = cuda_tpb_bpg_2d(Nz, Nr)

        # Check that m has a valid value
        if (m in [p-1, p, p+1]) == False:
            raise ValueError('m must be either p-1, p or p+1')

        # Register values of the arguments
        self.p = p
        self.m = m
        self.Nr = Nr
        self.rmax = rmax

        # Calculate the zeros of the Bessel function
        if m !=0:
            # In this case, 0 is a zero of the Bessel function of order m.
            # It turns out that it is needed to reconstruct the signal for p=0.
            alphas = np.hstack( (np.array([0.]), jn_zeros(m, Nr-1)) )
        else:
            alphas = jn_zeros(m, Nr)

        # Calculate the spectral grid
        self.nu = 1./(2*np.pi*rmax) * alphas

        # Calculate the spatial grid (Uniform grid with an half-cell offset)
        self.r = (rmax*1./Nr) * ( np.arange(Nr) + 0.5 )

        # Calculate and store the inverse matrix invM
        # (imposed by the constraints on the DHT of Bessel modes)
        # NB: When compared with the FBPIC article, all the matrices here
        # are calculated in transposed form. This is done so as to use the
        # `dot` and `gemm` functions, in the `transform` method.
        self.invM = np.empty((Nr, Nr))
        if p == m:
            p_denom = p+1
        else:
            p_denom = p
        denom = np.pi * rmax**2 * jn( p_denom, alphas)**2
        num = jn( p, 2*np.pi* self.r[np.newaxis,:]*self.nu[:,np.newaxis] )
        # Get the inverse matrix
        if m!=0:
            self.invM[1:, :] = num[1:, :] / denom[1:, np.newaxis]
            if p==m-1:
                self.invM[0, :] = self.r**(m-1) * 1./( np.pi * rmax**(m+1) )
            else:
                self.invM[0, :] = 0.
        else :
            self.invM[:, :] = num[:, :] / denom[:, np.newaxis]

        # Calculate the matrix M
        self.M = np.empty((N, N))
        if m !=0 and p != m-1 :
            self.M[:, 1:] = np.linalg.pinv( self.invM[1:, :] )
            self.M[:, 0] = 0.
        else :
            self.M = np.linalg.inv( self.invM )

        # Copy the arrays to the GPU if needed
        if self.use_cuda:
            # Conversion to complex and Fortran order
            # is needed for the cuBlas API
            self.d_M = cuda.to_device(
                np.asfortranarray( self.M, dtype=np.complex128 ) )
            self.d_invM = cuda.to_device(
                np.asfortranarray( self.invM, dtype=np.complex128 ) )


    def get_r(self):
        """
        Return the r grid

        Returns:
        ---------
        A real 1darray containing the values of the positions
        """
        return( self.r )


    def get_nu(self):
        """
        Return the natural, non-uniform nu grid

        Returns:
        ---------
        A real 1darray containing the values of the frequencies
        """
        return( self.nu )


    def transform( self, F, G ):
        """
        Perform the Hankel transform of F.

        Parameters:
        ------------
        F: 2darray of complex values
        Array containing the discrete values of the function for which
        the discrete Hankel transform is to be calculated.

        G: 2darray of complex values
        Array where the result will be stored
        """
        # Perform the matrix product with M
        if self.use_cuda:
            # Check that the shapes agree
            if (F.shape!=self.d_in.shape) or (G.shape!=self.d_out.shape):
                raise ValueError('The shape of F or G is different from '
                                 'the shape chosen at initialization.')
            # Convert the C-order F array to the Fortran-order d_in array
            cuda_copy_2d_to_2d[self.dim_grid, self.dim_block]( F, self.d_in )
            # Perform the matrix product using cuBlas
            self.blas.gemm( 'N', 'N', F.shape[0], F.shape[1],
                   F.shape[1], 1.0, self.d_in, self.d_M, 0., self.d_out )
            # Convert the Fortran-order d_out array to the C-order G array
            cuda_copy_2d_to_2d[self.dim_grid, self.dim_block]( self.d_out, G )

        else:
            np.dot( F, self.M, out=G )


    def inverse_transform( self, G, F ):
        """
        Performs the MDHT of G and stores the result in F
        Reference: see the paper associated with FBPIC

        G: 2darray of real or complex values
        Array containing the values from which to compute the DHT

        F: 2darray of real or complex values
        Array where the result will be stored
        """
        # Perform the matrix product with invM
        if self.use_cuda:
            # Check that the shapes agree
            if (G.shape!=self.d_in.shape) or (F.shape!=self.d_out.shape):
                raise ValueError('The shape of F or G is different from '
                                 'the shape chosen at initialization.')
            # Convert the C-order G array to the Fortran-order d_in array
            cuda_copy_2d_to_2d[self.dim_grid, self.dim_block](G, self.d_in )
            # Perform the matrix product using cuBlas
            self.blas.gemm( 'N', 'N', G.shape[0], G.shape[1],
                   G.shape[1], 1.0, self.d_in, self.d_invM, 0., self.d_out )
            # Convert the Fortran-order d_out array to the C-order G array
            cuda_copy_2d_to_2d[self.dim_grid, self.dim_block]( self.d_out, F )

        else:
            np.dot( G, self.invM, out=F )
