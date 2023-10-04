import os
import json
import shutil
import folder_paths
import execution
import re

def extend_config(default_config, user_config):
  cfg = {}
  for key, value in default_config.items():
    if key not in user_config:
      cfg[key] = value
    elif isinstance(value, dict):
      cfg[key] = extend_config(value, user_config[key])
    else:
      cfg[key] = user_config[key] if key in user_config else value
  return cfg

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(THIS_DIR, 'rgthree_config.json.default')
with open(DEFAULT_CONFIG_FILE, 'r', encoding = 'UTF-8') as file:
  config = re.sub(r"(?:^|\s)//.*", "", file.read(), flags=re.MULTILINE)
  rgthree_config_default = json.loads(config)

CONFIG_FILE = os.path.join(THIS_DIR, 'rgthree_config.json')
if os.path.exists(CONFIG_FILE):
  with open(CONFIG_FILE, 'r', encoding = 'UTF-8') as file:
    config = re.sub(r"(?:^|\s)//.*", "", file.read(), flags=re.MULTILINE)
    rgthree_config_user = json.loads(config)
else:
  rgthree_config_user = {}

RGTHREE_CONFIG = extend_config(rgthree_config_default, rgthree_config_user)


# Add 'saved_prompts' as a folder for Power Prompt node.
folder_paths.folder_names_and_paths['saved_prompts'] = ([], set(['.txt']))


if 'patch_recursive_execution' in RGTHREE_CONFIG and RGTHREE_CONFIG['patch_recursive_execution']:
  # Alright, I don't like doing this, but until https://github.com/comfyanonymous/ComfyUI/issues/1502
  # and/or https://github.com/comfyanonymous/ComfyUI/pull/1503 is pulled into ComfyUI, we need a way
  # to optimize the recursion that happens on prompt eval. This is particularly important for
  # rgthree nodes because workflows can contain many context nodes, but the problem would exist for
  # other nodes' (like "pipe" nodes, efficieny nodes). With `Context Big` nodes being
  # introduced, the number of input recursion that happens in these methods is exponential with a
  # saving of 1000's of percentage points over.

  msg = "\n\33[33m[rgthree] Optimizing ComfyUI reursive execution. If queueing and/or re-queueing seems "
  msg += "broken, change \"patch_recursive_execution\" to false in rgthree_config.json \33[0m"
  print(msg)

  class RgthreePatchRecursiveExecute_Set_patch_recursive_execution_to_false_if_not_working:
    """A fake 'list' that the caller for recursive_will_execute expects but we override such that
    `len(inst)` will return the count number, and `inst[-1]` will return the unique_id. Since that
    all the caller cares about, we can save several minutes and many MB of ram by simply counting
    numbers instead of concatenating a list of millions (only to count it). However the caller
    expects such a list, so we fake it with this.

    This mimics the enhancement from https://github.com/rgthree/ComfyUI/commit/50b3fb1 but without
    modifying the execution.py
    """
    def __init__(self, unique_id):
      self.unique_id = unique_id
      self.count = 0

    def add(self, value):
      self.count += value

    def __getitem__(self, key):
      """Returns the `unique_id` with '-1' since that's what the caller expects."""
      if key == -1:
        return self.unique_id
      # This one would future proof the proposed changes, in that case "0" is the count
      if key == 0:
        return self.count
      else:
        return -1

    def __len__(self):
      """Returns the "count" of the "list" as if we were building up a list instea of just
      incrementing `count`.
      """
      return self.count

    # The following (hopefully) future proofs if https://github.com/rgthree/ComfyUI/commit/50b3fb1
    # goes in, which changes from using `len` on a list, to sort directly (and, thus "<" and ">").
    def __gt__(self, other):
      return self.count > other

    def __lt__(self, other):
      return self.count < other

    def __str__(self):
      return str((self.count, self.unique_id,))


  execution.rgthree_cache_recursive_output_delete_if_changed_output = {}
  execution.rgthree_cache_recursive_will_execute = {}

  def rgthree_execute(self, *args, **kwargs):
    # When we execute, we'll reset our global cache here.
    execution.rgthree_cache_recursive_output_delete_if_changed_output = {}
    execution.rgthree_cache_recursive_will_execute = {}
    return self.old_execute(*args, **kwargs)


  def rgthree_recursive_will_execute(prompt, outputs, current_item, *args, **kwargs):
    unique_id = current_item
    inputs = prompt[unique_id]['inputs']
    will_execute = RgthreePatchRecursiveExecute_Set_patch_recursive_execution_to_false_if_not_working(unique_id)
    if unique_id in outputs:
        return will_execute

    will_execute.add(1)
    for x in inputs:
        input_data = inputs[x]
        if isinstance(input_data, list):
          input_unique_id = input_data[0]
          output_index = input_data[1]
          node_output_cache_key = f'{input_unique_id}.{output_index}'
          will_execute_value = None
          # If this node's output has already been recursively evaluated, then we can reuse.
          if node_output_cache_key in execution.rgthree_cache_recursive_will_execute:
            will_execute_value = execution.rgthree_cache_recursive_will_execute[node_output_cache_key]
          elif input_unique_id not in outputs:
            will_execute_value = execution.recursive_will_execute(prompt, outputs, input_unique_id, *args, **kwargs)
            execution.rgthree_cache_recursive_will_execute[node_output_cache_key] = will_execute_value
          if will_execute_value is not None:
            will_execute.add(len(will_execute_value))
    return will_execute


  def rgthree_recursive_output_delete_if_changed(prompt, old_prompt, outputs, current_item, *args, **kwargs):
    unique_id = current_item
    inputs = prompt[unique_id]['inputs']
    class_type = prompt[unique_id]['class_type']
    class_def = execution.nodes.NODE_CLASS_MAPPINGS[class_type]

    is_changed_old = ''
    is_changed = ''
    to_delete = False
    if hasattr(class_def, 'IS_CHANGED'):
      if unique_id in old_prompt and 'is_changed' in old_prompt[unique_id]:
        is_changed_old = old_prompt[unique_id]['is_changed']
      if 'is_changed' not in prompt[unique_id]:
        input_data_all = execution.get_input_data(inputs, class_def, unique_id, outputs)
        if input_data_all is not None:
          try:
            #is_changed = class_def.IS_CHANGED(**input_data_all)
            is_changed = execution.map_node_over_list(class_def, input_data_all, "IS_CHANGED")
            prompt[unique_id]['is_changed'] = is_changed
          except:
            to_delete = True
      else:
        is_changed = prompt[unique_id]['is_changed']

    if unique_id not in outputs:
      return True

    if not to_delete:
      if is_changed != is_changed_old:
        to_delete = True
      elif unique_id not in old_prompt:
        to_delete = True
      elif inputs == old_prompt[unique_id]['inputs']:
        for x in inputs:
          input_data = inputs[x]

          if isinstance(input_data, list):
            input_unique_id = input_data[0]
            output_index = input_data[1]
            node_output_cache_key = f'{input_unique_id}.{output_index}'
            # If this node's output has already been recursively evaluated, then we can stop.
            if node_output_cache_key in execution.rgthree_cache_recursive_output_delete_if_changed_output:
              to_delete = execution.rgthree_cache_recursive_output_delete_if_changed_output[
                node_output_cache_key]
            elif input_unique_id in outputs:
              to_delete = execution.recursive_output_delete_if_changed(prompt, old_prompt, outputs,
                                                                      input_unique_id, *args, **kwargs)
              execution.rgthree_cache_recursive_output_delete_if_changed_output[
                node_output_cache_key] = to_delete
            else:
              to_delete = True
            if to_delete:
              break
      else:
        to_delete = True

    if to_delete:
      d = outputs.pop(unique_id)
      del d
    return to_delete


  execution.PromptExecutor.old_execute = execution.PromptExecutor.execute
  execution.PromptExecutor.execute = rgthree_execute

  execution.old_recursive_output_delete_if_changed = execution.recursive_output_delete_if_changed
  execution.recursive_output_delete_if_changed = rgthree_recursive_output_delete_if_changed

  execution.old_recursive_will_execute = execution.recursive_will_execute
  execution.recursive_will_execute = rgthree_recursive_will_execute
