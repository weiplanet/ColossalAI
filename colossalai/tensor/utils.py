import torch

from typing import Iterator, Tuple, Union
import torch.nn as nn
from colossalai.tensor.colo_tensor import ColoTensor


def all_gather_simulator(target_pair):
    '''
    Simulating all-gather operation, analyze the communication cost
    and simulate the influence of the DimSpec.

    We don't allow uncontiguous layout, such as all-gather(S012)->S02 is NOT allowed.
    Therefore, all gather operation just remove the last element in shard list,
    e.g.: 
        all-gather(S01) -> S0

    Argument:
        target_pair(Tuple[int, List[int]]): The first element is the dimension of tensor to be sharded,
        and the second element decribes which logical axis will be sharded in that dimension.
    '''
    _, shard_list = target_pair
    new_shard_list = shard_list[:-1]

    return new_shard_list


def all_to_all_simulator(f_target_pair, b_target_pair):
    '''
    Simulating all-to-all operation, analyze the communication cost
    and simulate the influence of the DimSpec.

    We BANNED all representations which shard_list in decreasing order,
    such as S10, so all-to-all(S0, S1) -> RS01 is NOT allowed. 
    Therefore, if the behind shard_list is not None, we just extend it to the front shard_list.
    Argument:
        target_pair(Tuple[int, List[int]]): The first element is the dimension of tensor to be sharded,
        and the second element decribes which logical axis will be sharded in that dimension.
    e.g.: 
        all-to-all(S0, S1) -> [S01, R]
        all-to-all(S0, R) -> [R, S0]
    Otherwise, we extend the front shard_list to behind.
    e.g.: 
        all-to-all(R, S1) -> [S1, R]
    
    Argument:
        target_pair(Tuple[int, List[int]]): The first element is the dimension of tensor to be sharded,
        and the second element decribes which logical axis will be sharded in that dimension.
    '''
    _, f_shard_list = f_target_pair
    _, b_shard_list = b_target_pair
    if not len(b_shard_list):
        b_shard_list.extend(f_shard_list)
        f_shard_list = []
    else:
        f_shard_list.extend(b_shard_list)
        b_shard_list = []

    return f_shard_list, b_shard_list


def shard_simulator(target_pair, legal_sharding_dims):
    '''
    Simulating shard operation, analyze the communication cost(always ZERO)
    and simulate the influence of the DimSpec.

    We don't allow uncontiguous layout, such as shard(S0)->S02 is NOT allowed.
    In addition, We BANNED all representations which shard_list in decreasing order, 
    such as S10, so shard(S0) -> S10 is NOT allowed.
    Therefore, for the R dimension, we could just append any legal sharding dim on it.
    e.g.:
        shard(R) -> S0
    For the S dimension, we need to make sure the shard_list after sharding still keep rising order.
    e.g:
        shard(S0) -> S01

    Argument:
        target_pair(Tuple[int, List[int]]): The first element is the dimension of tensor to be sharded,
        and the second element decribes which logical axis will be sharded in that dimension.
    '''
    _, shard_list = target_pair
    shard_list_list = []
    for dim in legal_sharding_dims:
        if len(shard_list) != 0 and dim <= shard_list[-1]:
            continue
        new_shard_list = shard_list + [dim]
        shard_list_list.append(new_shard_list)

    return shard_list_list


# The function is credited to PyTorch Team
def named_params_with_colotensor(
    module: nn.Module,
    prefix: str = '',
    recurse: bool = True,
) -> Iterator[Tuple[str, Union[nn.Parameter, ColoTensor]]]:
    r"""Returns an iterator over module parameters (together with the
    ColoTensor parameters), yielding both the name of the parameter
    as well as the parameter itself. This is typically passed to a
    :class:torchshard._shard.sharded_optim.ShardedOptimizer

    Args:
        prefix (str): prefix to prepend to all parameter names.
        recurse (bool): if True, then yields parameters of this module
            and all submodules. Otherwise, yields only parameters that
            are direct members of this module.

    Yields:
        (string, Union[Tensor, ColoTensor]): Tuple containing
            the name and parameter (or ColoTensor parameter)

    Example:

        >>> model = torch.nn.Linear(*linear_size)
        >>> delattr(model.weight)
        >>> setattr(model.weight, ColoTensor(...))
        >>> for name, param in named_params_with_colotensor(model):
        >>>    if name in ['weight']:
        >>>        print(param.size())

    """
    modules = module.named_modules(prefix=prefix) if recurse else [(prefix, module)]

    memo = set()
    for mod_prefix, mod in modules:
        # find all sharded tensor params
        for name, val in vars(mod).items():
            if isinstance(val, ColoTensor) and val not in memo:
                memo.add(val)
                name = mod_prefix + ('.' if mod_prefix else '') + name
                yield name, val

    # find all nn.Parameters
    for name, val in module.named_parameters():
        yield name, val


def _convert_tensor(tensor: torch.Tensor) -> ColoTensor:
    return ColoTensor(tensor)


def convert_parameter(module: torch.nn.Module, param_name: str):
    # Perform some validation first.
    if not hasattr(module, param_name):
        raise ValueError(f'module: {module} does not have parameter with name: {param_name}')

    tensor = getattr(module, param_name)
    if not isinstance(tensor, torch.Tensor):
        raise ValueError(
            f'Expected {type(module).__name__}.{param_name} to be a Tensor, but found {type(tensor).__name__}')

    if not tensor.is_contiguous():
        raise ValueError(f'param: {param_name} is not a contiguous Tensor')

    st = _convert_tensor(tensor)

    # Replace param with ColoTensor.

    # Need to delete the attribute first since param_name might be
    # torch.nn.Parameter and can't be replaced with ColoTensor which is
    # not torch.nn.Parameter.
    delattr(module, param_name)

    # Now we can set the attribute appropriately.
    setattr(module, param_name, st)
