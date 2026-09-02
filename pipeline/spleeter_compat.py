"""
Compatibility patch to run Spleeter (last released in 2021, built against
TF1-style `tf.compat.v1.keras`) on a modern TensorFlow 2.x runtime.

Import this module BEFORE importing `spleeter` anywhere else in the
process (it's imported first thing in transcribe.py).

Two independent problems are patched here:

1. Spleeter's model code does
       from tensorflow.compat.v1.keras.initializers import he_uniform
   which is no longer importable as a real submodule on modern TF (it's a
   lazy-loaded attribute, not a package). We register fake modules in
   sys.modules so the `from ... import ...` statement succeeds.

2. Modern Keras (3.x) auto-generates layer names slightly differently than
   the TF1-era Keras that produced Spleeter's pretrained checkpoints. For
   models with several structurally-identical branches (Spleeter builds one
   U-Net per instrument), this can shift some auto-generated names one
   notch (e.g. checkpoint has "batch_normalization_1", our rebuilt graph
   produces "batch_normalization_1_1"). We patch the checkpoint restore to
   match on a collapsed name when the exact name isn't found, rather than
   failing outright. Variables with no checkpoint counterpart at all (e.g.
   Keras 3's internal "seed_generator" state) are left at their random
   initialization -- harmless for inference.
"""
import re
import sys
import types

import tensorflow as tf


def _install_tf1_keras_shim() -> None:
    keras_mod = types.ModuleType("tensorflow.compat.v1.keras")
    init_mod = types.ModuleType("tensorflow.compat.v1.keras.initializers")
    init_mod.he_uniform = tf.keras.initializers.he_uniform
    layers_mod = types.ModuleType("tensorflow.compat.v1.keras.layers")
    # CuDNNLSTM was removed from modern TF; plain LSTM auto-selects the
    # cuDNN-accelerated kernel when running on GPU, so it's a safe stand-in.
    layers_mod.CuDNNLSTM = tf.keras.layers.LSTM

    keras_mod.initializers = init_mod
    keras_mod.layers = layers_mod

    sys.modules["tensorflow.compat.v1.keras"] = keras_mod
    sys.modules["tensorflow.compat.v1.keras.initializers"] = init_mod
    sys.modules["tensorflow.compat.v1.keras.layers"] = layers_mod


def _patch_checkpoint_restore() -> None:
    from spleeter.model.provider import ModelProvider
    from spleeter.separator import Separator

    def _get_session(self):
        if self._session is None:
            provider = ModelProvider.default()
            model_directory = provider.get(self._params["model_dir"])
            latest_checkpoint = tf.train.latest_checkpoint(model_directory)

            from tensorflow.python.training import py_checkpoint_reader
            reader = py_checkpoint_reader.NewCheckpointReader(latest_checkpoint)
            ckpt_names = set(reader.get_variable_to_shape_map().keys())

            var_list = {}
            for v in tf.compat.v1.global_variables():
                base = v.name.split(":")[0]
                if base in ckpt_names:
                    var_list[base] = v
                    continue
                candidate = base
                for _ in range(5):
                    collapsed = re.sub(r"(_\d+)\1$", r"\1", candidate)
                    if collapsed == candidate:
                        break
                    candidate = collapsed
                    if candidate in ckpt_names:
                        var_list[candidate] = v
                        break

            saver = tf.compat.v1.train.Saver(var_list=var_list)
            self._session = tf.compat.v1.Session()
            self._session.run(tf.compat.v1.global_variables_initializer())
            saver.restore(self._session, latest_checkpoint)
        return self._session

    Separator._get_session = _get_session


_install_tf1_keras_shim()
_patch_checkpoint_restore()
