import math
from math import sqrt
from typing import List, Tuple

import torch

from kornia.core import Tensor, as_tensor, stack, tensor, zeros
from kornia.testing import KORNIA_CHECK, KORNIA_CHECK_IS_TENSOR, KORNIA_CHECK_SHAPE


def normalize_kernel2d(input: Tensor) -> Tensor:
    r"""Normalize both derivative and smoothing kernel."""
    if len(input.size()) < 2:
        raise TypeError(f"input should be at least 2D tensor. Got {input.size()}")
    norm: Tensor = input.abs().sum(dim=-1).sum(dim=-1)
    return input / (norm.unsqueeze(-1).unsqueeze(-1))


def gaussian(window_size: int, sigma: float) -> torch.Tensor:
    sigma_t = torch.tensor(sigma)

    return gaussian_t(window_size, sigma_t)


def gaussian_t(window_size: int, sigma: torch.Tensor) -> torch.Tensor:
    device, dtype = sigma.device, sigma.dtype
    sigma = sigma.unsqueeze(-1)
    batch_size = sigma.shape[0]
    x = (torch.arange(window_size, device=device, dtype=dtype) - window_size // 2).expand(batch_size, -1)
    if window_size % 2 == 0:
        x = x + 0.5
    gauss = torch.exp(-x.pow(2.0) / (2 * sigma.pow(2.0)))
    return gauss / gauss.sum(-1, keepdim=True)


def gaussian_discrete_erf(window_size: int, sigma) -> Tensor:
    r"""Discrete Gaussian by interpolating the error function.

    Adapted from: https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py
    """
    device = sigma.device if isinstance(sigma, Tensor) else None
    sigma = as_tensor(sigma, dtype=torch.float, device=device)
    x = torch.arange(window_size).float() - window_size // 2
    t = 0.70710678 / torch.abs(sigma)
    gauss = 0.5 * ((t * (x + 0.5)).erf() - (t * (x - 0.5)).erf())
    gauss = gauss.clamp(min=0)
    return gauss / gauss.sum()


def _modified_bessel_0(x: Tensor) -> Tensor:
    r"""Adapted from:

    https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py
    """
    if torch.abs(x) < 3.75:
        y = (x / 3.75) * (x / 3.75)
        return 1.0 + y * (
            3.5156229 + y * (3.0899424 + y * (1.2067492 + y * (0.2659732 + y * (0.360768e-1 + y * 0.45813e-2))))
        )
    ax = torch.abs(x)
    y = 3.75 / ax
    ans = 0.916281e-2 + y * (-0.2057706e-1 + y * (0.2635537e-1 + y * (-0.1647633e-1 + y * 0.392377e-2)))
    coef = 0.39894228 + y * (0.1328592e-1 + y * (0.225319e-2 + y * (-0.157565e-2 + y * ans)))
    return (torch.exp(ax) / torch.sqrt(ax)) * coef


def _modified_bessel_1(x: Tensor) -> Tensor:
    r"""adapted from:

    https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py
    """
    if torch.abs(x) < 3.75:
        y = (x / 3.75) * (x / 3.75)
        ans = 0.51498869 + y * (0.15084934 + y * (0.2658733e-1 + y * (0.301532e-2 + y * 0.32411e-3)))
        return torch.abs(x) * (0.5 + y * (0.87890594 + y * ans))
    ax = torch.abs(x)
    y = 3.75 / ax
    ans = 0.2282967e-1 + y * (-0.2895312e-1 + y * (0.1787654e-1 - y * 0.420059e-2))
    ans = 0.39894228 + y * (-0.3988024e-1 + y * (-0.362018e-2 + y * (0.163801e-2 + y * (-0.1031555e-1 + y * ans))))
    ans = ans * torch.exp(ax) / torch.sqrt(ax)
    return -ans if x < 0.0 else ans


def _modified_bessel_i(n: int, x: Tensor) -> Tensor:
    r"""adapted from:

    https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py
    """
    if n < 2:
        raise ValueError("n must be greater than 1.")
    if x == 0.0:
        return x
    device = x.device
    tox = 2.0 / torch.abs(x)
    ans = tensor(0.0, device=device)
    bip = tensor(0.0, device=device)
    bi = tensor(1.0, device=device)
    m = int(2 * (n + int(sqrt(40.0 * n))))
    for j in range(m, 0, -1):
        bim = bip + float(j) * tox * bi
        bip = bi
        bi = bim
        if abs(bi) > 1.0e10:
            ans = ans * 1.0e-10
            bi = bi * 1.0e-10
            bip = bip * 1.0e-10
        if j == n:
            ans = bip
    ans = ans * _modified_bessel_0(x) / bi
    return -ans if x < 0.0 and (n % 2) == 1 else ans


def gaussian_discrete(window_size, sigma) -> Tensor:
    r"""Discrete Gaussian kernel based on the modified Bessel functions.

    Adapted from: https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py
    """
    device = sigma.device if isinstance(sigma, Tensor) else None
    sigma = as_tensor(sigma, dtype=torch.float, device=device)
    sigma2 = sigma * sigma
    tail = int(window_size // 2) + 1
    bessels = [
        _modified_bessel_0(sigma2),
        _modified_bessel_1(sigma2),
        *(_modified_bessel_i(k, sigma2) for k in range(2, tail)),
    ]

    out = stack(bessels[:0:-1] + bessels, 0) * torch.exp(sigma2)

    return out / out.sum()


def laplacian_1d(window_size) -> Tensor:
    r"""One could also use the Laplacian of Gaussian formula to design the filter."""

    filter_1d = torch.ones(window_size)
    filter_1d[window_size // 2] = 1 - window_size
    laplacian_1d: Tensor = filter_1d
    return laplacian_1d


def get_box_kernel2d(kernel_size: Tuple[int, int]) -> Tensor:
    r"""Utility function that returns a box filter."""
    kx: float = float(kernel_size[0])
    ky: float = float(kernel_size[1])
    scale: Tensor = tensor(1.0) / tensor([kx * ky])
    tmp_kernel: Tensor = torch.ones(1, kernel_size[0], kernel_size[1])
    return scale.to(tmp_kernel.dtype) * tmp_kernel


def get_binary_kernel2d(window_size: Tuple[int, int]) -> Tensor:
    r"""Create a binary kernel to extract the patches.

    If the window size is HxW will create a (H*W)xHxW kernel.
    """
    window_range: int = window_size[0] * window_size[1]
    kernel: Tensor = zeros(window_range, window_range)
    for i in range(window_range):
        kernel[i, i] += 1.0
    return kernel.view(window_range, 1, window_size[0], window_size[1])


def get_sobel_kernel_3x3() -> Tensor:
    """Utility function that returns a sobel kernel of 3x3."""
    return tensor([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]])


def get_sobel_kernel_5x5_2nd_order() -> Tensor:
    """Utility function that returns a 2nd order sobel kernel of 5x5."""
    return tensor(
        [
            [-1.0, 0.0, 2.0, 0.0, -1.0],
            [-4.0, 0.0, 8.0, 0.0, -4.0],
            [-6.0, 0.0, 12.0, 0.0, -6.0],
            [-4.0, 0.0, 8.0, 0.0, -4.0],
            [-1.0, 0.0, 2.0, 0.0, -1.0],
        ]
    )


def _get_sobel_kernel_5x5_2nd_order_xy() -> Tensor:
    """Utility function that returns a 2nd order sobel kernel of 5x5."""
    return tensor(
        [
            [-1.0, -2.0, 0.0, 2.0, 1.0],
            [-2.0, -4.0, 0.0, 4.0, 2.0],
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [2.0, 4.0, 0.0, -4.0, -2.0],
            [1.0, 2.0, 0.0, -2.0, -1.0],
        ]
    )


def get_diff_kernel_3x3() -> Tensor:
    """Utility function that returns a first order derivative kernel of 3x3."""
    return tensor([[-0.0, 0.0, 0.0], [-1.0, 0.0, 1.0], [-0.0, 0.0, 0.0]])


def get_diff_kernel3d(device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    """Utility function that returns a first order derivative kernel of 3x3x3."""
    kernel: Tensor = tensor(
        [
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [-0.5, 0.0, 0.5], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, -0.5, 0.0], [0.0, 0.0, 0.0], [0.0, 0.5, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [0.0, -0.5, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 0.0]],
            ],
        ],
        device=device,
        dtype=dtype,
    )
    return kernel.unsqueeze(1)


def get_diff_kernel3d_2nd_order(device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    """Utility function that returns a first order derivative kernel of 3x3x3."""
    kernel: Tensor = tensor(
        [
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [1.0, -2.0, 1.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 1.0, 0.0], [0.0, -2.0, 0.0], [0.0, 1.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, -2.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[1.0, 0.0, -1.0], [0.0, 0.0, 0.0], [-1.0, 0.0, 1.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            ],
            [
                [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.0, -1.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, -1.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            ],
            [
                [[0.0, 0.0, 0.0], [1.0, 0.0, -1.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
                [[0.0, 0.0, 0.0], [-1.0, 0.0, 1.0], [0.0, 0.0, 0.0]],
            ],
        ],
        device=device,
        dtype=dtype,
    )
    return kernel.unsqueeze(1)


def get_sobel_kernel2d() -> Tensor:
    kernel_x: Tensor = get_sobel_kernel_3x3()
    kernel_y: Tensor = kernel_x.transpose(0, 1)
    return stack([kernel_x, kernel_y])


def get_diff_kernel2d() -> Tensor:
    kernel_x: Tensor = get_diff_kernel_3x3()
    kernel_y: Tensor = kernel_x.transpose(0, 1)
    return stack([kernel_x, kernel_y])


def get_sobel_kernel2d_2nd_order() -> Tensor:
    gxx: Tensor = get_sobel_kernel_5x5_2nd_order()
    gyy: Tensor = gxx.transpose(0, 1)
    gxy: Tensor = _get_sobel_kernel_5x5_2nd_order_xy()
    return stack([gxx, gxy, gyy])


def get_diff_kernel2d_2nd_order() -> Tensor:
    gxx: Tensor = tensor([[0.0, 0.0, 0.0], [1.0, -2.0, 1.0], [0.0, 0.0, 0.0]])
    gyy: Tensor = gxx.transpose(0, 1)
    gxy: Tensor = tensor([[-1.0, 0.0, 1.0], [0.0, 0.0, 0.0], [1.0, 0.0, -1.0]])
    return stack([gxx, gxy, gyy])


def get_spatial_gradient_kernel2d(mode: str, order: int) -> Tensor:
    r"""Function that returns kernel for 1st or 2nd order image gradients, using one of the following operators:

    sobel, diff.
    """
    if mode not in ['sobel', 'diff']:
        raise TypeError(
            "mode should be either sobel\
                         or diff. Got {}".format(
                mode
            )
        )
    if order not in [1, 2]:
        raise TypeError(
            "order should be either 1 or 2\
                         Got {}".format(
                order
            )
        )
    if mode == 'sobel' and order == 1:
        kernel: Tensor = get_sobel_kernel2d()
    elif mode == 'sobel' and order == 2:
        kernel = get_sobel_kernel2d_2nd_order()
    elif mode == 'diff' and order == 1:
        kernel = get_diff_kernel2d()
    elif mode == 'diff' and order == 2:
        kernel = get_diff_kernel2d_2nd_order()
    else:
        raise NotImplementedError("")
    return kernel


def get_spatial_gradient_kernel3d(mode: str, order: int, device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    r"""Function that returns kernel for 1st or 2nd order scale pyramid gradients, using one of the following
    operators: sobel, diff."""
    if mode not in ['sobel', 'diff']:
        raise TypeError(
            "mode should be either sobel\
                         or diff. Got {}".format(
                mode
            )
        )
    if order not in [1, 2]:
        raise TypeError(
            "order should be either 1 or 2\
                         Got {}".format(
                order
            )
        )
    if mode == 'sobel':
        raise NotImplementedError("Sobel kernel for 3d gradient is not implemented yet")
    if mode == 'diff' and order == 1:
        kernel = get_diff_kernel3d(device, dtype)
    elif mode == 'diff' and order == 2:
        kernel = get_diff_kernel3d_2nd_order(device, dtype)
    else:
        raise NotImplementedError("")
    return kernel


def get_gaussian_kernel1d(kernel_size: int, sigma: float, force_even: bool = False) -> Tensor:
    r"""Function that returns Gaussian filter coefficients.

    Args:
        kernel_size: filter size. It should be odd and positive.
        sigma: gaussian standard deviation.
        force_even: overrides requirement for odd kernel size.

    Returns:
        gaussian filter coefficients

    Shape:
        - Output: :math:`(B, \text{kernel_size})`.

    Examples:

        >>> get_gaussian_kernel1d(3, 2.5)
        tensor([[0.3243, 0.3513, 0.3243]])

        >>> get_gaussian_kernel1d(5, 1.5)
        tensor([[0.1201, 0.2339, 0.2921, 0.2339, 0.1201]])
    """
    sigma_t = torch.tensor(sigma).unsqueeze(0)

    return get_gaussian_kernel1d_t(kernel_size, sigma_t, force_even)


def get_gaussian_kernel1d_t(kernel_size: int, sigma: torch.Tensor, force_even: bool = False) -> torch.Tensor:
    r"""Function that returns Gaussian filter coefficients.

    Args:
        kernel_size: filter size. It should be odd and positive.
        sigma: gaussian standard deviation with shape of
          :math:`(B)`.
        force_even: overrides requirement for odd kernel size.

    Returns:
        gaussian filter coefficients

    Shape:
        - Output: :math:`(B, \text{kernel_size})`.

    Examples:

        >>> get_gaussian_kernel1d_t(3, torch.tensor([2.5]))
        tensor([[0.3243, 0.3513, 0.3243]])

        >>> get_gaussian_kernel1d_t(5, torch.tensor([1.5]))
        tensor([[0.1201, 0.2339, 0.2921, 0.2339, 0.1201]])

        >>> get_gaussian_kernel1d_t(5, torch.tensor([1.5, 0.7]))
        tensor([[0.1201, 0.2339, 0.2921, 0.2339, 0.1201],
                [0.0096, 0.2054, 0.5699, 0.2054, 0.0096]])
    """

    if not isinstance(kernel_size, int) or ((kernel_size % 2 == 0) and not force_even) or (kernel_size <= 0):
        raise TypeError("kernel_size must be an odd positive integer. " "Got {}".format(kernel_size))

    window_1d: torch.Tensor = gaussian_t(kernel_size, sigma)
    return window_1d


def get_gaussian_discrete_kernel1d(kernel_size: int, sigma: float, force_even: bool = False) -> Tensor:
    r"""Function that returns Gaussian filter coefficients based on the modified Bessel functions. Adapted from:
    https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py.

    Args:
        kernel_size: filter size. It should be odd and positive.
        sigma: gaussian standard deviation.
        force_even: overrides requirement for odd kernel size.

    Returns:
        1D tensor with gaussian filter coefficients.

    Shape:
        - Output: :math:`(\text{kernel_size})`

    Examples:

        >>> get_gaussian_discrete_kernel1d(3, 2.5)
        tensor([0.3235, 0.3531, 0.3235])

        >>> get_gaussian_discrete_kernel1d(5, 1.5)
        tensor([0.1096, 0.2323, 0.3161, 0.2323, 0.1096])
    """
    if not isinstance(kernel_size, int) or ((kernel_size % 2 == 0) and not force_even) or (kernel_size <= 0):
        raise TypeError("kernel_size must be an odd positive integer. " "Got {}".format(kernel_size))
    window_1d = gaussian_discrete(kernel_size, sigma)
    return window_1d


def get_gaussian_erf_kernel1d(kernel_size: int, sigma: float, force_even: bool = False) -> Tensor:
    r"""Function that returns Gaussian filter coefficients by interpolating the error function, adapted from:
    https://github.com/Project-MONAI/MONAI/blob/master/monai/networks/layers/convutils.py.

    Args:
        kernel_size: filter size. It should be odd and positive.
        sigma: gaussian standard deviation.
        force_even: overrides requirement for odd kernel size.

    Returns:
        1D tensor with gaussian filter coefficients.

    Shape:
        - Output: :math:`(\text{kernel_size})`

    Examples:

        >>> get_gaussian_erf_kernel1d(3, 2.5)
        tensor([0.3245, 0.3511, 0.3245])

        >>> get_gaussian_erf_kernel1d(5, 1.5)
        tensor([0.1226, 0.2331, 0.2887, 0.2331, 0.1226])
    """
    if not isinstance(kernel_size, int) or ((kernel_size % 2 == 0) and not force_even) or (kernel_size <= 0):
        raise TypeError("kernel_size must be an odd positive integer. " "Got {}".format(kernel_size))
    window_1d = gaussian_discrete_erf(kernel_size, sigma)
    return window_1d


def get_gaussian_kernel2d(
    kernel_size: Tuple[int, int], sigma: Tuple[float, float], force_even: bool = False
) -> torch.Tensor:
    r"""Function that returns Gaussian filter matrix coefficients.

    Args:
        kernel_size: filter sizes in the x and y direction.
         Sizes should be odd and positive.
        sigma: gaussian standard deviation in the x and y.
        force_even: overrides requirement for odd kernel size.

    Returns:
        2D tensor with gaussian filter matrix coefficients.

    Shape:
        - Output: :math:`(B, \text{kernel_size}_x, \text{kernel_size}_y)`

    Examples:

        >>> get_gaussian_kernel2d((5, 5), (1.5, 1.5))
        tensor([[[0.0144, 0.0281, 0.0351, 0.0281, 0.0144],
                 [0.0281, 0.0547, 0.0683, 0.0547, 0.0281],
                 [0.0351, 0.0683, 0.0853, 0.0683, 0.0351],
                 [0.0281, 0.0547, 0.0683, 0.0547, 0.0281],
                 [0.0144, 0.0281, 0.0351, 0.0281, 0.0144]]])

        >>> get_gaussian_kernel2d((3, 5), (1.5, 1.5))
        tensor([[[0.0370, 0.0720, 0.0899, 0.0720, 0.0370],
                 [0.0462, 0.0899, 0.1123, 0.0899, 0.0462],
                 [0.0370, 0.0720, 0.0899, 0.0720, 0.0370]]])
    """

    sigma_t = torch.tensor(sigma).unsqueeze(0)

    return get_gaussian_kernel2d_t(kernel_size, sigma_t, force_even)


def get_gaussian_kernel2d_t(
    kernel_size: Tuple[int, int], sigma: torch.Tensor, force_even: bool = False
) -> torch.Tensor:
    r"""Function that returns Gaussian filter matrix coefficients.

    Args:
        kernel_size: filter sizes in the x and y direction.
         Sizes should be odd and positive.
        sigma: gaussian standard deviation in the x and y
         direction with shape of :math:`(B, 2)`.
        force_even: overrides requirement for odd kernel size.

    Returns:
        2D tensor with gaussian filter matrix coefficients.

    Shape:
        - Output: :math:`(B, \text{kernel_size}_x, \text{kernel_size}_y)`

    Examples:

        >>> get_gaussian_kernel2d_t((5, 5), torch.tensor([[1.5, 1.5]]))
        tensor([[[0.0144, 0.0281, 0.0351, 0.0281, 0.0144],
                 [0.0281, 0.0547, 0.0683, 0.0547, 0.0281],
                 [0.0351, 0.0683, 0.0853, 0.0683, 0.0351],
                 [0.0281, 0.0547, 0.0683, 0.0547, 0.0281],
                 [0.0144, 0.0281, 0.0351, 0.0281, 0.0144]]])

        >>> get_gaussian_kernel2d_t((3, 5), torch.tensor([[1.5, 1.5]]))
        tensor([[[0.0370, 0.0720, 0.0899, 0.0720, 0.0370],
                 [0.0462, 0.0899, 0.1123, 0.0899, 0.0462],
                 [0.0370, 0.0720, 0.0899, 0.0720, 0.0370]]])

        >>> get_gaussian_kernel2d_t((5, 5), torch.tensor([[1.5, 1.5], [0.5, 0.5]]))
        tensor([[[1.4419e-02, 2.8084e-02, 3.5073e-02, 2.8084e-02, 1.4419e-02],
                 [2.8084e-02, 5.4700e-02, 6.8312e-02, 5.4700e-02, 2.8084e-02],
                 [3.5073e-02, 6.8312e-02, 8.5312e-02, 6.8312e-02, 3.5073e-02],
                 [2.8084e-02, 5.4700e-02, 6.8312e-02, 5.4700e-02, 2.8084e-02],
                 [1.4419e-02, 2.8084e-02, 3.5073e-02, 2.8084e-02, 1.4419e-02]],
        <BLANKLINE>
                [[6.9625e-08, 2.8089e-05, 2.0755e-04, 2.8089e-05, 6.9625e-08],
                 [2.8089e-05, 1.1332e-02, 8.3731e-02, 1.1332e-02, 2.8089e-05],
                 [2.0755e-04, 8.3731e-02, 6.1869e-01, 8.3731e-02, 2.0755e-04],
                 [2.8089e-05, 1.1332e-02, 8.3731e-02, 1.1332e-02, 2.8089e-05],
                 [6.9625e-08, 2.8089e-05, 2.0755e-04, 2.8089e-05, 6.9625e-08]]])
    """
    if not isinstance(kernel_size, tuple) or len(kernel_size) != 2:
        raise TypeError(f"kernel_size must be a tuple of length two. Got {kernel_size}")

    KORNIA_CHECK_SHAPE(sigma, ["B", "2"])

    ksize_x, ksize_y = kernel_size
    sigma_x, sigma_y = sigma[:, 0], sigma[:, 1]
    kernel_x: torch.Tensor = get_gaussian_kernel1d_t(ksize_x, sigma_x, force_even)
    kernel_y: torch.Tensor = get_gaussian_kernel1d_t(ksize_y, sigma_y, force_even)
    kernel_2d: torch.Tensor = torch.matmul(kernel_x.unsqueeze(-1), kernel_y.unsqueeze(-1).transpose(2, 1))
    return kernel_2d


def get_gaussian_kernel3d(
    kernel_size: Tuple[int, int, int], sigma: Tuple[float, float, float], force_even: bool = False
) -> Tensor:
    r"""Function that returns Gaussian filter matrix coefficients.

    Args:
        kernel_size: filter sizes in the x, y and z direction.
         Sizes should be odd and positive.
        sigma: gaussian standard deviation in the x, y and z
         direction.
        force_even: overrides requirement for odd kernel size.

    Returns:
        3D tensor with gaussian filter matrix coefficients.

    Shape:
        - Output: :math:`(B, \text{kernel_size}_x, \text{kernel_size}_y,  \text{kernel_size}_z)`

    Examples:
        >>> get_gaussian_kernel3d((3, 3, 3), (1.5, 1.5, 1.5))
        tensor([[[[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]],
        <BLANKLINE>
                 [[0.0364, 0.0455, 0.0364],
                  [0.0455, 0.0568, 0.0455],
                  [0.0364, 0.0455, 0.0364]],
        <BLANKLINE>
                 [[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]]]])
        >>> get_gaussian_kernel3d((3, 3, 3), (1.5, 1.5, 1.5)).sum()
        tensor(1.)
        >>> get_gaussian_kernel3d((3, 3, 3), (1.5, 1.5, 1.5)).shape
        torch.Size([1, 3, 3, 3])
    """
    sigma_t = torch.tensor(sigma).unsqueeze(0)

    return get_gaussian_kernel3d_t(kernel_size, sigma_t, force_even)


def get_gaussian_kernel3d_t(
    kernel_size: Tuple[int, int, int], sigma: torch.Tensor, force_even: bool = False
) -> torch.Tensor:
    r"""Function that returns Gaussian filter matrix coefficients.

    Args:
        kernel_size: filter sizes in the x, y and z direction.
         Sizes should be odd and positive.
        sigma: gaussian standard deviation in the x, y and z
         direction with shape of :math:`(B, 3)`.
        force_even: overrides requirement for odd kernel size.

    Returns:
        3D tensor with gaussian filter matrix coefficients.

    Shape:
        - Output: :math:`(B, \text{kernel_size}_x, \text{kernel_size}_y, \text{kernel_size}_z)`

    Examples:

        >>> get_gaussian_kernel3d_t((3, 3, 3), torch.tensor([[1.5, 1.5, 1.5]]))
        tensor([[[[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]],
        <BLANKLINE>
                 [[0.0364, 0.0455, 0.0364],
                  [0.0455, 0.0568, 0.0455],
                  [0.0364, 0.0455, 0.0364]],
        <BLANKLINE>
                 [[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]]]])
        >>> get_gaussian_kernel3d_t((3, 7, 5), torch.tensor([[1.5, 1.5, 1.5]])).shape
        torch.Size([1, 3, 7, 5])

        >>> get_gaussian_kernel3d_t((3, 3, 3), torch.tensor([[1.5, 1.5, 1.5], [0.5, 0.5, 0.5]]))
        tensor([[[[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]],
        <BLANKLINE>
                 [[0.0364, 0.0455, 0.0364],
                  [0.0455, 0.0568, 0.0455],
                  [0.0364, 0.0455, 0.0364]],
        <BLANKLINE>
                 [[0.0292, 0.0364, 0.0292],
                  [0.0364, 0.0455, 0.0364],
                  [0.0292, 0.0364, 0.0292]]],
        <BLANKLINE>
        <BLANKLINE>
                [[[0.0012, 0.0089, 0.0012],
                  [0.0089, 0.0660, 0.0089],
                  [0.0012, 0.0089, 0.0012]],
        <BLANKLINE>
                 [[0.0089, 0.0660, 0.0089],
                  [0.0660, 0.4874, 0.0660],
                  [0.0089, 0.0660, 0.0089]],
        <BLANKLINE>
                 [[0.0012, 0.0089, 0.0012],
                  [0.0089, 0.0660, 0.0089],
                  [0.0012, 0.0089, 0.0012]]]])
    """
    KORNIA_CHECK(
        isinstance(kernel_size, tuple) or len(kernel_size) != 3,
        f"kernel_size must be a tuple of length three. Got {kernel_size}",
    )
    KORNIA_CHECK_IS_TENSOR(sigma, "signma must be a tensor")
    KORNIA_CHECK_SHAPE(sigma, ["B", "3"])

    ksize_x, ksize_y, ksize_z = kernel_size
    sigma_x, sigma_y, sigma_z = sigma[:, 0], sigma[:, 1], sigma[:, 2]
    kernel_x: Tensor = get_gaussian_kernel1d_t(ksize_x, sigma_x, force_even)
    kernel_y: Tensor = get_gaussian_kernel1d_t(ksize_y, sigma_y, force_even)
    kernel_z: Tensor = get_gaussian_kernel1d_t(ksize_z, sigma_z, force_even)
    kernel_2d: torch.Tensor = kernel_x[..., None] @ kernel_y[..., None].transpose(2, 1)
    kernel_3d: Tensor = (kernel_z[:, None, :, None] @ (kernel_2d[..., None].transpose(3, 2))).transpose(3, 2)
    return kernel_3d


def get_laplacian_kernel1d(kernel_size: int) -> Tensor:
    r"""Function that returns the coefficients of a 1D Laplacian filter.

    Args:
        kernel_size: filter size. It should be odd and positive.

    Returns:
        1D tensor with laplacian filter coefficients.

    Shape:
        - Output: math:`(\text{kernel_size})`

    Examples:
        >>> get_laplacian_kernel1d(3)
        tensor([ 1., -2.,  1.])
        >>> get_laplacian_kernel1d(5)
        tensor([ 1.,  1., -4.,  1.,  1.])
    """
    if not isinstance(kernel_size, int) or kernel_size % 2 == 0 or kernel_size <= 0:
        raise TypeError(f"ksize must be an odd positive integer. Got {kernel_size}")
    window_1d: Tensor = laplacian_1d(kernel_size)
    return window_1d


def get_laplacian_kernel2d(kernel_size: int) -> Tensor:
    r"""Function that returns Gaussian filter matrix coefficients.

    Args:
        kernel_size: filter size should be odd.

    Returns:
        2D tensor with laplacian filter matrix coefficients.

    Shape:
        - Output: :math:`(\text{kernel_size}_x, \text{kernel_size}_y)`

    Examples:
        >>> get_laplacian_kernel2d(3)
        tensor([[ 1.,  1.,  1.],
                [ 1., -8.,  1.],
                [ 1.,  1.,  1.]])
        >>> get_laplacian_kernel2d(5)
        tensor([[  1.,   1.,   1.,   1.,   1.],
                [  1.,   1.,   1.,   1.,   1.],
                [  1.,   1., -24.,   1.,   1.],
                [  1.,   1.,   1.,   1.,   1.],
                [  1.,   1.,   1.,   1.,   1.]])
    """
    if not isinstance(kernel_size, int) or kernel_size % 2 == 0 or kernel_size <= 0:
        raise TypeError(f"ksize must be an odd positive integer. Got {kernel_size}")

    kernel = torch.ones((kernel_size, kernel_size))
    mid = kernel_size // 2
    kernel[mid, mid] = 1 - kernel_size**2
    kernel_2d: Tensor = kernel
    return kernel_2d


def get_pascal_kernel_2d(kernel_size: int, norm: bool = True) -> Tensor:
    """Generate pascal filter kernel by kernel size.

    Args:
        kernel_size: height and width of the kernel.
        norm: if to normalize the kernel or not. Default: True.

    Returns:
        kernel shaped as :math:`(kernel_size, kernel_size)`

    Examples:
    >>> get_pascal_kernel_2d(1)
    tensor([[1.]])
    >>> get_pascal_kernel_2d(4)
    tensor([[0.0156, 0.0469, 0.0469, 0.0156],
            [0.0469, 0.1406, 0.1406, 0.0469],
            [0.0469, 0.1406, 0.1406, 0.0469],
            [0.0156, 0.0469, 0.0469, 0.0156]])
    >>> get_pascal_kernel_2d(4, norm=False)
    tensor([[1., 3., 3., 1.],
            [3., 9., 9., 3.],
            [3., 9., 9., 3.],
            [1., 3., 3., 1.]])
    """
    a = get_pascal_kernel_1d(kernel_size)

    filt = a[:, None] * a[None, :]
    if norm:
        filt = filt / torch.sum(filt)
    return filt


def get_pascal_kernel_1d(kernel_size: int, norm: bool = False) -> Tensor:
    """Generate Yang Hui triangle (Pascal's triangle) by a given number.

    Args:
        kernel_size: height and width of the kernel.
        norm: if to normalize the kernel or not. Default: False.

    Returns:
        kernel shaped as :math:`(kernel_size,)`

    Examples:
    >>> get_pascal_kernel_1d(1)
    tensor([1.])
    >>> get_pascal_kernel_1d(2)
    tensor([1., 1.])
    >>> get_pascal_kernel_1d(3)
    tensor([1., 2., 1.])
    >>> get_pascal_kernel_1d(4)
    tensor([1., 3., 3., 1.])
    >>> get_pascal_kernel_1d(5)
    tensor([1., 4., 6., 4., 1.])
    >>> get_pascal_kernel_1d(6)
    tensor([ 1.,  5., 10., 10.,  5.,  1.])
    """
    pre: List[float] = []
    cur: List[float] = []
    for i in range(kernel_size):
        cur = [1.0] * (i + 1)

        for j in range(1, i // 2 + 1):
            value = pre[j - 1] + pre[j]
            cur[j] = value
            if i != 2 * j:
                cur[-j - 1] = value
        pre = cur

    out = as_tensor(cur)
    if norm:
        out = out / torch.sum(out)
    return out


def get_canny_nms_kernel(device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    """Utility function that returns 3x3 kernels for the Canny Non-maximal suppression."""
    kernel: Tensor = tensor(
        [
            [[0.0, 0.0, 0.0], [0.0, 1.0, -1.0], [0.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
            [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, -1.0, 0.0]],
            [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], [-1.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
            [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
            [[0.0, -1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
            [[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]],
        ],
        device=device,
        dtype=dtype,
    )
    return kernel.unsqueeze(1)


def get_hysteresis_kernel(device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    """Utility function that returns the 3x3 kernels for the Canny hysteresis."""
    kernel: Tensor = tensor(
        [
            [[0.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            [[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            [[0.0, 1.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            [[0.0, 0.0, 1.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        ],
        device=device,
        dtype=dtype,
    )
    return kernel.unsqueeze(1)


def get_hanning_kernel1d(kernel_size: int, device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    r"""Returns Hanning (also known as Hann) kernel, used in signal processing and KCF tracker.

    .. math::  w(n) = 0.5 - 0.5cos\\left(\\frac{2\\pi{n}}{M-1}\\right)
               \\qquad 0 \\leq n \\leq M-1

    See further in numpy docs https://numpy.org/doc/stable/reference/generated/numpy.hanning.html

    Args:
        kernel_size: The size the of the kernel. It should be positive.

    Returns:
        1D tensor with Hanning filter coefficients.
            .. math::  w(n) = 0.5 - 0.5cos\\left(\\frac{2\\pi{n}}{M-1}\\right)

    Shape:
        - Output: math:`(\text{kernel_size})`

    Examples:
        >>> get_hanning_kernel1d(4)
        tensor([0.0000, 0.7500, 0.7500, 0.0000])
    """
    if not isinstance(kernel_size, int) or kernel_size <= 2:
        raise TypeError(f"ksize must be an positive integer > 2. Got {kernel_size}")

    x: Tensor = torch.arange(kernel_size, device=device, dtype=dtype)
    x = 0.5 - 0.5 * torch.cos(2.0 * math.pi * x / float(kernel_size - 1))
    return x


def get_hanning_kernel2d(kernel_size: Tuple[int, int], device=torch.device('cpu'), dtype=torch.float) -> Tensor:
    r"""Returns 2d Hanning kernel, used in signal processing and KCF tracker.

    Args:
        kernel_size: The size of the kernel for the filter. It should be positive.

    Returns:
        2D tensor with Hanning filter coefficients.
            .. math::  w(n) = 0.5 - 0.5cos\\left(\\frac{2\\pi{n}}{M-1}\\right)

    Shape:
        - Output: math:`(\text{kernel_size[0], kernel_size[1]})`
    """
    if kernel_size[0] <= 2 or kernel_size[1] <= 2:
        raise TypeError(f"ksize must be an tuple of positive integers > 2. Got {kernel_size}")
    ky: Tensor = get_hanning_kernel1d(kernel_size[0], device, dtype)[None].T
    kx: Tensor = get_hanning_kernel1d(kernel_size[1], device, dtype)[None]
    kernel2d = ky @ kx
    return kernel2d
