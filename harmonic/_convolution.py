import torch, itertools, math
import torch.nn as nn
import torch.nn.functional as F
from torch_localize import localized_module
from torch_dimcheck import dimchecked, ShapeChecker

from .cmplx import cmplx 
from .weights import Weights

@dimchecked
def cconv_nd(x: ['b',     'f_in', 'hx', 'wx', ..., 2],
             w: ['f_out', 'f_in', 'hk', 'wk', ..., 2],
             dim=2, pad=False) -> ['b', 'f_out', 'ho', 'wo', ..., 2]:

    if dim not in [2, 3]:
        raise ValueError("Dim can only be 2 or 3, got {}".format(dim))

    if pad:
        padding = w.shape[3] // 2
    else:
        padding = 0

    conv = F.conv3d if dim == 3 else F.conv2d

    real = conv(x[..., 0], w[..., 0], padding=padding) - \
           conv(x[..., 1], w[..., 1], padding=padding)

    imag = conv(x[..., 0], w[..., 1], padding=padding) + \
           conv(x[..., 1], w[..., 0], padding=padding)

    return cmplx(real, imag)


def ords2s(in_ord, out_ord):
    return '{}_{}'.format(in_ord, out_ord)


class _HConv(nn.Module):
    def __init__(self, in_repr, out_repr, size, radius=None, dim=2, pad=False):
        super(_HConv, self).__init__()

        if dim not in [2, 3]:
            raise ValueError("Dim can only be 2 or 3, got {}".format(dim))

        self.dim = dim
        self.in_repr = in_repr
        self.out_repr = out_repr
        self.size = size
        self.pad = pad

        self.radius = radius if radius is not None else size / 2 - 1
        self.weights = nn.ModuleDict()

        # 2d convolutions take features_in feature maps as input,
        # 3d take features_in * size feature maps (features_in times
        # size of z-dimension)
        mul = 1 if dim == 2 else size

        # create Weights for each (input order, output order) pair
        for (in_ord, in_mult), (out_ord, out_mult) in itertools.product(
                                    enumerate(in_repr),
                                    enumerate(out_repr)):

            if in_mult == 0 or out_mult == 0:
                # either order is not represented in current (in, out) pair
                continue

            name = 'Weights {}x{} -> {}x{}'.format(
                in_mult, in_ord, out_mult, out_ord
            )


            order_diff = in_ord - out_ord
            weight = Weights(
                in_mult * mul, out_mult, size, self.radius, order_diff, name=name
            )
            self.weights[ords2s(in_ord, out_ord)] = weight 

    def forward(self, x: ['b', 'fi', 'hx', 'wx', 2]) -> ['b', 'fo', 'ho', 'wo', 2]:
        spatial_unsqueeze = [self.size] * self.dim

        input_kernels = []
        for (in_ord, in_mult) in enumerate(self.in_repr):
            output_kernels = []
            if in_mult == 0:
                continue

            for (out_ord, out_mult) in enumerate(self.out_repr):
                if out_mult == 0:
                    continue

                ix = ords2s(in_ord, out_ord)
                kernel = self.weights[ix].cartesian_harmonics()
                kernel = kernel.reshape(
                    out_mult, in_mult, *spatial_unsqueeze, 2
                )

                output_kernels.append(kernel)

            input_kernels.append(torch.cat(output_kernels, dim=0))

        kernels = torch.cat(input_kernels, dim=1)

        return cconv_nd(x, kernels, dim=self.dim, pad=self.pad)
