import os.path as osp

import pytest
import torch
import torch.multiprocessing as mp

from colossalai.utils.model.pipelinable import PipelinableContext

from functools import partial
from colossalai.utils import free_port
from colossalai.testing import rerun_on_exception

NUM_CHUNKS = 1
PIPELINE_SIZE = 2


class MLP(torch.nn.Module):

    def __init__(self, dim: int = 256):
        super().__init__()
        intermediate_dim = dim * 4
        self.dense_1 = torch.nn.Linear(dim, intermediate_dim)
        self.activation = torch.nn.GELU()
        self.dense_2 = torch.nn.Linear(intermediate_dim, dim)
        self.dropout = torch.nn.Dropout(0.1)

    def forward(self, x):
        x = self.dense_1(x)
        x = self.activation(x)
        x = self.dense_2(x)
        x = self.dropout(x)
        return x


def run_pipelinable(rank):
    pipelinable = PipelinableContext()
    with pipelinable:
        model = MLP()

    assert pipelinable.policy == "balanced"
    pipelinable.load_policy("uniform")
    assert pipelinable.policy == "uniform"
    pipelinable.to_layer_list()

    assert pipelinable.layers_count == len(list(model.children()))

    pipeline_model_part_0 = pipelinable.partition(NUM_CHUNKS, PIPELINE_SIZE, 0)
    assert isinstance(pipeline_model_part_0, torch.nn.Module)
    pipeline_model_part_1 = pipelinable.partition(NUM_CHUNKS, PIPELINE_SIZE, 1)
    assert isinstance(pipeline_model_part_1, torch.nn.Module)

    layers_count_in_part_0 = len(list(pipeline_model_part_0._module_list))
    layers_count_in_part_1 = len(list(pipeline_model_part_1._module_list))

    assert layers_count_in_part_0 + layers_count_in_part_1 == pipelinable.layers_count


@rerun_on_exception(exception_type=mp.ProcessRaisedException, pattern=".*Address already in use.*")
def test_pipelinable():
    mp.spawn(run_pipelinable, nprocs=1)


if __name__ == '__main__':
    test_pipelinable()
