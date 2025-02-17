import colossalai
import torch
import pytest
import torch.nn as nn
import torch.multiprocessing as mp
from colossalai.tensor import ColoTensor
from colossalai.tensor import dist_spec
from colossalai.tensor import TensorSpec, ComputePattern, ParallelAction, DistSpecManager
from colossalai.context import ParallelMode
from colossalai.testing import rerun_if_address_is_in_use
from colossalai.utils import free_port
from functools import partial
from colossalai.core import global_context as gpc


class Conv1D(nn.Module):
    """
    1D-convolutional layer as defined by Radford et al. for OpenAI GPT (and also used in GPT-2).
    Basically works like a linear layer but the weights are transposed.
    Args:
        nf (`int`): The number of output features.
        nx (`int`): The number of input features.
    """

    def __init__(self, nf, nx):
        super().__init__()
        self.nf = nf
        w = torch.empty(nx, nf)
        nn.init.normal_(w, std=0.02)
        self.weight = nn.Parameter(w)
        self.bias = nn.Parameter(torch.ones(nf))

    def forward(self, x):
        size_out = x.size()[:-1] + (self.nf,)
        x = torch.addmm(self.bias, x.view(-1, x.size(-1)), self.weight)
        x = x.view(size_out)
        return x


def init_1d_row(weight, bias):
    spec = TensorSpec(
        dist_spec.shard(gpc.get_group(ParallelMode.PARALLEL_1D), [0], [gpc.get_world_size(ParallelMode.PARALLEL_1D)]),
        [ParallelAction(priority=1, compute_pattern=ComputePattern.TP1DRow, parallel_mode=ParallelMode.PARALLEL_1D)])
    with DistSpecManager.no_grad():
        weight.set_spec(spec)


def check_grad_1d_row(model: torch.nn.Module, weight, bias):
    rank = gpc.get_local_rank(ParallelMode.PARALLEL_1D)
    size = gpc.get_world_size(ParallelMode.PARALLEL_1D)
    assert torch.allclose(model.weight.grad.chunk(size, 0)[rank], weight.grad)
    assert torch.allclose(model.bias.grad, bias.grad)


def init_1d_col(weight, bias):
    spec = TensorSpec(
        dist_spec.shard(gpc.get_group(ParallelMode.PARALLEL_1D), [-1], [gpc.get_world_size(ParallelMode.PARALLEL_1D)]),
        [ParallelAction(priority=1, compute_pattern=ComputePattern.TP1DCol, parallel_mode=ParallelMode.PARALLEL_1D)])
    with DistSpecManager.no_grad():
        weight.set_spec(spec)
        bias.set_spec(spec)


def check_grad_1d_col(model: torch.nn.Module, weight, bias):
    rank = gpc.get_local_rank(ParallelMode.PARALLEL_1D)
    size = gpc.get_world_size(ParallelMode.PARALLEL_1D)
    assert torch.allclose(model.weight.grad.chunk(size, -1)[rank], weight.grad)
    assert torch.allclose(model.bias.grad.chunk(size, -1)[rank], bias.grad)


def run_with_spec(spec_init_func, check_grad_func):
    model = Conv1D(4, 16).cuda()
    weight = ColoTensor.init_from_torch_tensor(torch.nn.Parameter(model.weight.detach()))
    bias = ColoTensor.init_from_torch_tensor(torch.nn.Parameter(model.bias.detach()))
    spec_init_func(weight, bias)
    x = torch.rand(2, 16).cuda()
    out = model(x)
    colo_out = torch.addmm(bias, x, weight)
    assert torch.allclose(out, colo_out)
    grad = torch.rand_like(out)
    out.backward(grad)
    colo_out.backward(grad)
    check_grad_func(model, weight, bias)


def run_dist(rank, world_size, port):
    config = dict(parallel=dict(tensor=dict(mode="1d", size=world_size),))
    colossalai.launch(config=config, rank=rank, world_size=world_size, host='localhost', port=port, backend='nccl')
    run_with_spec(init_1d_row, check_grad_1d_row)
    run_with_spec(init_1d_col, check_grad_1d_col)


@pytest.mark.dist
@pytest.mark.parametrize('world_size', [1, 4])
@rerun_if_address_is_in_use()
def test_addmm_1d(world_size):
    run_func = partial(run_dist, world_size=world_size, port=free_port())
    mp.spawn(run_func, nprocs=world_size)


if __name__ == '__main__':
    test_addmm_1d(4)
