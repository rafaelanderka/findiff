import itertools
import numbers
import operator

import scipy.sparse as sparse

from findiff.coefs import coefficients, coefficients_non_uni
from .stencils import StencilSet
from .utils import *

DEFAULT_ACC = 2
DEFAULT_PERIODICITY = False


class Operator(object):
    """ Base class for all operator classes """
    pass


class BinaryOperator(Operator):
    """ Base class for all binary operators (like addition) that allow to combine or chain differential operators """

    def __init__(self, left, right):
        self.left = left
        self.right = right

    def apply(self, rhs, *args, **kwargs):
        raise NotImplementedError

    def __call__(self, rhs, *args, **kwargs):
        return self.apply(rhs, *args, **kwargs)

    def stencil(self, shape):
        return StencilSet(self, shape)


class Plus(BinaryOperator):
    """ A class to add two differential operators """

    def __init__(self, left, right):
        super().__init__(left, right)

    def __add__(self, other):
        return Plus(self, other)

    def __radd__(self, other):
        return Plus(self, other)

    def __sub__(self, other):
        return Minus(self, other)

    def __rsub__(self, other):
        return Minus(self, other)

    def __mul__(self, other):
        return Mul(self, other)

    def __rmul__(self, other):
        return Mul(self, other)

    def apply(self, rhs, *args, **kwargs):

        if isinstance(self.right, LinearMap) or isinstance(self.right, BinaryOperator):
            right = self.right.apply(rhs, *args, **kwargs)
        else:
            right = self.right

        if isinstance(self.left, LinearMap) or isinstance(self.left, BinaryOperator):
            left = self.left.apply(rhs, *args, **kwargs)
        else:
            left = self.left * rhs

        return left + right

    def matrix(self, shape, *args, **kwargs):
        left, right = self.left, self.right
        if isinstance(self.left, Operator):
            left = self.left.matrix(shape, *args, **kwargs)
        if isinstance(self.right, Operator):
            right = self.right.matrix(shape, *args, **kwargs)
        return left + right


class Minus(BinaryOperator):
    """ A class to subtract two differential operators from each other """

    def __init__(self, left, right):
        super().__init__(left, right)

    def __add__(self, other):
        return Plus(self, other)

    def __radd__(self, other):
        return Plus(self, other)

    def __sub__(self, other):
        return Minus(self, other)

    def __rsub__(self, other):
        return Minus(self, other)

    def __mul__(self, other):
        return Mul(self, other)

    def __rmul__(self, other):
        return Mul(self, other)

    def apply(self, rhs, *args, **kwargs):

        if isinstance(self.right, LinearMap) or isinstance(self.right, BinaryOperator):
            right = self.right.apply(rhs, *args, **kwargs)
        else:
            right = self.right

        if isinstance(self.left, LinearMap) or isinstance(self.left, BinaryOperator):
            left = self.left.apply(rhs, *args, **kwargs)
        else:
            left = self.left * rhs

        return left - right

    def matrix(self, shape, *args, **kwargs):
        left, right = self.left, self.right
        if isinstance(self.left, Operator):
            left = self.left.matrix(shape, *args, **kwargs)
        if isinstance(self.right, Operator):
            right = self.right.matrix(shape, *args, **kwargs)
        return left - right


class Mul(BinaryOperator):
    """ A class to multiply (chain) two differential operators """

    def __init__(self, left, right):
        super().__init__(left, right)
        self.oper = operator.mul

    def __add__(self, other):
        return Plus(self, other)

    def __radd__(self, other):
        return Plus(self, other)

    def __sub__(self, other):
        return Minus(self, other)

    def __rsub__(self, other):
        return Minus(self, other)

    def __mul__(self, other):
        return Mul(self, other)

    def __rmul__(self, other):
        return Mul(self, other)

    def apply(self, rhs, *args, **kwargs):

        if isinstance(self.right, LinearMap) or isinstance(self.right, BinaryOperator):
            result = self.right.apply(rhs, *args, **kwargs)
        else:
            result = self.right * rhs

        if isinstance(self.left, LinearMap) or isinstance(self.left, BinaryOperator):
            result = self.left.apply(result, *args, **kwargs)
        else:
            result = self.left * result

        return result

    def matrix(self, shape, *args, **kwargs):
        """ Matrix representation of given operator product on an equidistant grid of given shape.

        :param shape: tuple with the shape of the grid
        :return: scipy sparse matrix representing the operator product
        """

        if isinstance(self.left, np.ndarray):
            left = sparse.diags(self.left.reshape(-1), 0)
        elif isinstance(self.left, LinearMap) or isinstance(self.left, BinaryOperator):
            left = self.left.matrix(shape, *args, **kwargs)
        else:
            left = self.left * sparse.diags(np.ones(shape).reshape(-1), 0)

        if isinstance(self.right, np.ndarray):
            right = sparse.diags(self.right.reshape(-1), 0)
        elif isinstance(self.right, LinearMap) or isinstance(self.right, BinaryOperator):
            right = self.right.matrix(shape, *args, **kwargs)
        else:
            right = self.right * sparse.diags(np.ones(shape).reshape(-1), 0)

        return left.dot(right)


class LinearMap(Operator):

    def __init__(self, value):
        self.value = value

    def __add__(self, other):
        return Plus(self, other)

    def __radd__(self, other):
        return Plus(self, other)

    def __sub__(self, other):
        return Minus(self, other)

    def __rsub__(self, other):
        return Minus(self, other)

    def __mul__(self, other):
        return Mul(self, other)

    def __rmul__(self, other):
        return Mul(self, other)

    def __call__(self, rhs, *args, **kwargs):
        return self.apply(rhs, *args, **kwargs)


class Diff(LinearMap):
    """ Representation of a single partial derivative based on finite differences.

            This class is usually not used directly by the user, but is wrapped in
            a FinDiff object.

            :param axis: the numpy axis along which to apply the derivative

            :param order: the order of the derivative

            :param kwargs: optional keyword arguments

                Allowed keywords:

                    `acc`:  even integer
                            The desired accuracy order.
    """

    def __init__(self, axis, order, **kwargs):

        assert isinstance(axis, numbers.Integral) and axis >= 0
        assert isinstance(order, numbers.Integral) and order >= 0

        self.axis = axis
        self.order = order
        self.acc = None
        if 'acc' in kwargs:
            self.acc = kwargs['acc']
        if 'periodic' in kwargs:
            self.periodic = kwargs['periodic']

    def apply(self, u, *args, **kwargs):
        """ Applies the partial derivative to a numpy array."""

        h = None
        acc = DEFAULT_ACC

        def get_h(a):
            if isinstance(a, dict):
                h = a[self.axis]
            else:
                h = a
            return h

        for key, value in kwargs.items():
            if key == 'h' or key == 'grid':
                h = get_h(value)
                break

        if h is None:
            h = get_h(args[0])

        if 'acc' in kwargs:
            acc = kwargs['acc']

        if isinstance(h, np.ndarray):
            return self.diff_non_uni(u, h, **kwargs)

        return self.diff(u, h, acc)

    def diff(self, y, h, acc):
        """The core function to take a partial derivative on a uniform grid.

            Central coefficients will be used whenever possible. Backward or forward
            coefficients will be used if not enough points are available on either side,
            i.e. forward coefficients for the low index boundary and backward coefficients
            for the high index boundary.
        """

        dim = self.axis
        coefs = coefficients(self.order, acc)
        deriv = self.order

        try:
            npts = y.shape[dim]
        except AttributeError as err:
            raise ValueError(
                "FinDiff objects can only be applied to arrays or evaluated(!) functions returning arrays") from err

        scheme = "center"
        weights = coefs[scheme]["coefficients"]
        offsets = coefs[scheme]["offsets"]

        num_bndry_points = len(weights) // 2
        ref_slice = slice(num_bndry_points, npts - num_bndry_points, 1)
        off_slices = [self._shift_slice(ref_slice, offsets[k], npts) for k in range(len(offsets))]

        yd = np.zeros_like(y)

        self._apply_to_array(yd, y, weights, off_slices, ref_slice, dim)

        scheme = "forward"
        weights = coefs[scheme]["coefficients"]
        offsets = coefs[scheme]["offsets"]

        ref_slice = slice(0, num_bndry_points, 1)
        off_slices = [self._shift_slice(ref_slice, offsets[k], npts) for k in range(len(offsets))]

        self._apply_to_array(yd, y, weights, off_slices, ref_slice, dim)

        scheme = "backward"
        weights = coefs[scheme]["coefficients"]
        offsets = coefs[scheme]["offsets"]

        ref_slice = slice(npts - num_bndry_points, npts, 1)
        off_slices = [self._shift_slice(ref_slice, offsets[k], npts) for k in range(len(offsets))]

        self._apply_to_array(yd, y, weights, off_slices, ref_slice, dim)

        h_inv = 1. / h ** deriv
        return yd * h_inv

    def diff_non_uni(self, y, coords, **kwargs):
        """The core function to take a partial derivative on a non-uniform grid"""

        if "acc" in kwargs:
            acc = kwargs["acc"]
        elif self.acc is not None:
            acc = self.acc
        else:
            acc = 2

        order, dim = self.order, self.axis

        coef_list = []
        for i in range(len(coords)):
            coef_list.append(coefficients_non_uni(order, acc, coords, i))

        yd = np.zeros_like(y)

        ndims = len(y.shape)
        multi_slice = [slice(None, None)] * ndims
        ref_multi_slice = [slice(None, None)] * ndims

        for i, x in enumerate(coords):

            coefs = coef_list[i]
            weights = coefs["coefficients"]
            offsets = coefs["offsets"]
            ref_multi_slice[dim] = i

            for off, w in zip(offsets, weights):
                multi_slice[dim] = i + off
                yd[tuple(ref_multi_slice)] += w * y[tuple(multi_slice)]

        return yd

    def matrix(self, shape, h=None, acc=None, coords=None, sparse_type=sparse.csr_matrix):
        """ Matrix representation of the partial derivative.

                :param shape: Tuple with the shape of the grid (number of grid points in each dimension)

                :param h: The grid spacing for the axis of the partial derivative
                            (only used for uniform grids)

                :param coords: The coordinate values of the grid on the axis of the partial derivative
                            (only used for non-uniform grids)

                :param acc: The accuracy order of the derivative (even int)

                :param sparse_type: The scipy sparse matrix type used for the matrix representation.

                :returns matrix representation (scipy sparse matrix)
        """

        if h is not None:
            return sparse_type(self._matrix_uniform(shape, h, acc))
        elif coords is not None:
            return sparse_type(self._matrix_nonuniform(shape, coords, acc))
        else:
            raise ValueError('Neither spacing nor coordinates given.')

    def _matrix_nonuniform(self, shape, coords, acc):

        coords = coords[self.axis]

        siz = np.prod(shape)
        long_inds = np.arange(siz).reshape(shape)
        short_inds = [np.arange(shape[k]) for k in range(len(shape))]
        short_inds = list(itertools.product(*short_inds))

        coef_dicts = []
        for i in range(len(coords)):
            coef_dicts.append(coefficients_non_uni(self.order, acc, coords, i))

        mat = sparse.lil_matrix((siz, siz))

        for base_ind_long, base_ind_short in enumerate(short_inds):
            cd = coef_dicts[base_ind_short[self.axis]]
            cs, os = cd['coefficients'], cd['offsets']
            for c, o in zip(cs, os):
                off_short = np.zeros(len(shape), dtype=int)
                off_short[self.axis] = int(o)
                off_ind_short = np.array(base_ind_short, dtype=int) + off_short
                off_long = long_inds[tuple(off_ind_short)]

                mat[base_ind_long, off_long] += c

        return mat

    def _matrix_uniform(self, shape, h=None, acc=None):

        if isinstance(h, dict):
            h = h[self.axis]

        acc = self._properties(self.acc, acc, 2)

        shape = np.array(shape)
        ndims = len(shape)
        siz = np.prod(shape)
        long_indices_nd = long_indices_as_ndarray(shape)

        axis, order = self.axis, self.order
        mat = sparse.lil_matrix((siz, siz))
        coeff_dict = coefficients(order, acc)

        for scheme in ['center', 'forward', 'backward']:

            offsets_1d = coeff_dict[scheme]['offsets']
            coeffs = coeff_dict[scheme]['coefficients']

            # translate offsets of given scheme to long format
            offsets_long = []
            for o_1d in offsets_1d:
                o_nd = np.zeros(ndims, dtype=int)
                o_nd[axis] = o_1d
                offsets_long.append(o_nd)

            # determine points where to evaluate current scheme in long format
            nside = len(coeff_dict['center']['coefficients']) // 2
            if scheme == 'center':
                multi_slice = [slice(None, None)] * ndims
                if self.periodic:
                    multi_slice[axis] = slice(0, None)
                else:
                    multi_slice[axis] = slice(nside, -nside)
                Is = long_indices_nd[tuple(multi_slice)].reshape(-1)
            elif scheme == 'forward':
                if self.periodic:
                    multi_slice = [slice(0, 0)] * ndims
                else:
                    multi_slice = [slice(None, None)] * ndims
                    multi_slice[axis] = slice(0, nside)
                Is = long_indices_nd[tuple(multi_slice)].reshape(-1)
            elif scheme == 'backward':
                if self.periodic:
                    multi_slice = [slice(0, 0)] * ndims
                else:
                    multi_slice = [slice(None, None)] * ndims
                    multi_slice[axis] = slice(-nside, None)
                Is = long_indices_nd[tuple(multi_slice)].reshape(-1)
            Is_nd = np.array(np.unravel_index(Is, shape))

            for o, c in zip(offsets_long, coeffs):
                v = c / h ** order
                Is_o = np.ravel_multi_index((Is_nd + o[:,None]) % shape[:,None], shape)
                mat[Is, Is_o] = v

        return mat

    def set_accuracy(self, acc):
        self.acc = acc

    def _properties(self, self_value, value, default_value):

        if value is not None:
            return value
        elif self_value is None:
            return default_value
        else:
            return self_value

    def _apply_to_array(self, yd, y, weights, off_slices, ref_slice, dim):
        """Applies the finite differences only to slices along a given axis"""

        ndims = len(y.shape)

        all = slice(None, None, 1)

        ref_multi_slice = [all] * ndims
        ref_multi_slice[dim] = ref_slice

        for w, s in zip(weights, off_slices):
            off_multi_slice = [all] * ndims
            off_multi_slice[dim] = s
            if abs(1 - w) < 1.E-14:
                yd[tuple(ref_multi_slice)] += y[tuple(off_multi_slice)]
            else:
                yd[tuple(ref_multi_slice)] += w * y[tuple(off_multi_slice)]

    def _shift_slice(self, sl, off, max_index):

        if sl.start + off < 0 or sl.stop + off > max_index:
            raise IndexError("Shift slice out of bounds")

        return slice(sl.start + off, sl.stop + off, sl.step)


class Id(LinearMap):
    """ The identity operator. When applied to an array, returns the same array (not a copy) """

    def __init__(self):
        self.value = 1

    def apply(self, rhs, *args, **kwargs):
        return rhs

    def matrix(self, shape):
        """ Matrix representation of the identity operator, i.e. identity matrix of given shape.

        :param shape: Shape of the arrays to which Id shall be applied
        :type shape: tuple of ints
        :return: Sparse identity matrix.
        :rtype: scipy.sparse.csr_matrix
        """

        siz = np.prod(shape)
        mat = sparse.lil_matrix((siz, siz))
        diag = list(range(siz))
        mat[diag, diag] = 1
        return sparse.csr_matrix(mat)


class Coef(object):
    """
    Encapsulates a constant (number) or variable (N-dimensional coordinate array) value to multiply with a linear operator

    :param value: a number or an numpy.ndarray with meshed coordinates

    **Example**:

    The following example defines the differential operator

    .. math:: 2x\\frac{\\partial^3}{\\partial x^2\\partial z}

    >>> X, Y, Z, U = numpy.meshgrid(x, y, z, u, indexing="ij")
    >>> diff_op = Coef(2*X) * FinDiff((0, dx, 2), (2, dz, 1))

    """

    def __init__(self, value):
        self.value = value

    def __mul__(self, other):
        return Mul(self.value, other)
