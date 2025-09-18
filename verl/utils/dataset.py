# Copyright 2024 Bytedance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import os
from collections import defaultdict
from io import BytesIO
from typing import Any, Dict, List, Optional, Union

import numpy as np
import torch
from datasets import load_dataset
from PIL import Image
from PIL.Image import Image as ImageObject
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizer, ProcessorMixin

from ..models.transformers.qwen2_vl import get_rope_index
from . import torch_functional as VF
import json


import yaml
import pdb
from omegaconf import OmegaConf
from verl.utils.tokenizer import get_processor, get_tokenizer


def collate_fn(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    tensors = defaultdict(list)
    non_tensors = defaultdict(list)
    for feature in features:
        for key, value in feature.items():
            if isinstance(value, torch.Tensor):
                tensors[key].append(value)
            else:
                non_tensors[key].append(value)

    for key, value in tensors.items():
        tensors[key] = torch.stack(value, dim=0)

    for key, value in non_tensors.items():
        non_tensors[key] = np.array(value, dtype=object)

    return {**tensors, **non_tensors}


def process_image(image: Union[Dict[str, Any], ImageObject], max_pixels: int, min_pixels: int) -> ImageObject:
    if isinstance(image, dict):
        image = Image.open(BytesIO(image["bytes"]))
    if isinstance(image, str):
        image = Image.open(image)
    if (image.width * image.height) > max_pixels:
        resize_factor = math.sqrt(max_pixels / (image.width * image.height))
        width, height = int(image.width * resize_factor), int(image.height * resize_factor)
        image = image.resize((width, height))

    if (image.width * image.height) < min_pixels:
        resize_factor = math.sqrt(min_pixels / (image.width * image.height))
        width, height = int(image.width * resize_factor), int(image.height * resize_factor)
        image = image.resize((width, height))

    if image.mode != "RGB":
        image = image.convert("RGB")

    return image


class RLHFDataset(Dataset):
    """
    We assume the dataset contains a column that contains prompts and other information
    """

    def __init__(
        self,
        data_path: str,
        tokenizer: PreTrainedTokenizer,
        processor: Optional[ProcessorMixin],
        prompt_key: str = "prompt",
        answer_key: str = "answer",
        image_key: str = "images",
        max_prompt_length: int = 1024,
        truncation: str = "error",
        system_prompt: str = None,
        max_pixels: int = None,
        min_pixels: int = None,
    ):
        self.tokenizer = tokenizer
        self.processor = processor
        self.prompt_key = prompt_key
        self.answer_key = answer_key
        self.image_key = image_key
        self.max_prompt_length = max_prompt_length
        self.truncation = truncation
        self.system_prompt = system_prompt
        self.max_pixels = max_pixels
        self.min_pixels = min_pixels

        if "@" in data_path:
            data_path, data_split = data_path.split("@")
        else:
            data_split = "train"
            # print(data_path)

        if os.path.isdir(data_path):
            self.dataset = load_dataset("parquet", data_dir=data_path, split="train")
        elif os.path.isfile(data_path):
            self.dataset = load_dataset("parquet", data_files=data_path, split="train")
        else:  # remote dataset
            self.dataset = load_dataset(data_path, split=data_split)

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, index):
        row_dict: dict = self.dataset[index]

        # prompt_str: str = row_dict[self.prompt_key]
        text=row_dict['instruction']
        history=row_dict['history']
        task_type=row_dict['task_type']
        row_dict.pop('verify_bbox', None)
        row_dict.pop('success_rate', None)
        row_dict.pop('scale', None)
        images=[row_dict['image']]
      
        if task_type=='high':
            prompt_str=  (
                f"You are GUI-R1, a reasoning GUI Agent Assistant. In this UI screenshot <image>, I want you to continue executing the command '{text}', with the action history being '{history}'.\n"
                "Please provide the action to perform (enumerate from ['complete', 'close/delete', 'press_home', 'click', 'press_back', 'type', 'select', 'scroll', 'enter']), the point where the cursor is moved to (integer) if a click is performed, and any input text required to complete the action.\n"
                "Output the thinking process in <think> </think> tags, and the final answer in <answer> </answer> tags as follows:\n"
                "<think> ... </think> <answer>[{'action': enum['complete', 'close/delete', 'press_home', 'click', 'press_back', 'type', 'select', 'scroll', 'enter'], 'point': [x, y], 'input_text': 'no input text [default]'}]</answer>\n"
                "Note:\n specific input text (no default) is necessary for actions enum['type', 'select', 'scroll'] \n Example:\n"
                "[{'action': enum['complete', 'close/delete', 'press_home', 'press_back', 'enter'], 'point': [-100, -100], 'input_text': 'no input text'}]\n"
                "[{'action': enum['click'], 'point': [123, 300], 'input_text': 'no input text'}]\n"
                "[{'action': enum['type', 'select'], 'point': [-100, -100], 'input_text': 'shanghai shopping mall'}]\n"
                "[{'action': enum['scroll'], 'point': [-100, -100], 'input_text': enum['up', 'left', 'right', 'down']}]"
            )
        else:
            prompt_str=(
                f"You are GUI-R1, a reasoning GUI Agent Assistant. In this UI screenshot <image>, I want you to continue executing the command '{text}', with the action history being '{history}'.\n"
                "Please provide the action to perform (enumerate from ['click']), the point where the cursor is moved to (integer) if a click is performed, and any input text required to complete the action.\n"
                "Output the thinking process in <think> </think> tags, and the final answer in <answer> </answer> tags as follows:\n"
                "<think> ... </think> <answer>[{'action': enum[ 'click'], 'point': [x, y], 'input_text': 'no input text'}]</answer>\n"
                "Example:\n"
                "[{'action': enum['click'], 'point': [123, 300], 'input_text': 'no input text'}]\n"
            )
        messages = [{"role": "user", "content": prompt_str}]
        images=[process_image(image, self.max_pixels, self.min_pixels) for image in images]

        scalex,scaley=images[0].size
        gt_bbox=row_dict['gt_bbox']
        gt_bbox[0]*=scalex
        gt_bbox[1]*=scaley
        if len(gt_bbox)>2:
            gt_bbox[2]*=scalex
            gt_bbox[3]*=scaley

        gt={'action': row_dict['gt_action'],'gt_bbox': gt_bbox,'input_text': row_dict['gt_input_text']}
        # if self.system_prompt:
        #     messages.insert(0, {"role": "system", "content": self.system_prompt})

        prompt = self.tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)

        # if self.image_key in row_dict:
        prompt = prompt.replace("<image>", "<|vision_start|><|image_pad|><|vision_end|>")
        row_dict["multi_modal_data"] = {
            "image": images
        }
        model_inputs = self.processor(row_dict["multi_modal_data"]["image"], prompt, return_tensors="pt")
        input_ids = model_inputs.pop("input_ids")[0]
        attention_mask = model_inputs.pop("attention_mask")[0]
        row_dict["multi_modal_inputs"] = dict(model_inputs)
        position_ids = get_rope_index(
            self.processor,
            input_ids=input_ids,
            image_grid_thw=model_inputs["image_grid_thw"],
            attention_mask=attention_mask,
        )  # (3, seq_length)
        # else:
        #     model_inputs = self.tokenizer([prompt], add_special_tokens=False, return_tensors="pt")
        #     input_ids = model_inputs.pop("input_ids")[0]
        #     attention_mask = model_inputs.pop("attention_mask")[0]
        #     position_ids = torch.clip(attention_mask.cumsum(dim=0) - 1, min=0, max=None)  # (seq_length,)

        input_ids, attention_mask, position_ids = VF.postprocess_data(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            max_length=self.max_prompt_length,
            pad_token_id=self.tokenizer.pad_token_id,
            left_pad=True,
            truncation=self.truncation,
        )
        row_dict["input_ids"] = input_ids
        row_dict["attention_mask"] = attention_mask
        row_dict["position_ids"] = position_ids
        row_dict["raw_prompt_ids"] = self.tokenizer.encode(prompt, add_special_tokens=False)
        row_dict["ground_truth"] = json.dumps(gt)
        return row_dict
    


class Mind2WebDataset(Dataset):
    pass

    

if __name__ == "__main__":
    
    config_path =  "/home/fsq/gui_agent/GUI-R1/examples/config.yaml"
    # with open(config_path, "r") as f:
    #     config = yaml.safe_load(f)
    
    # train_dataset = RLHFDataset(
    #     data_path="/home/fsq/hf_home/hub/datasets--ritzzai--GUI-R1/snapshots/ca55ddaa180c5e8f8b27003221c391efa10a1f52/train.parquet",
    #     tokenizer=tokenizer,
    #     processor=processor,
    #     prompt_key=config["data"]["prompt_key"],
    #     answer_key=config["data"]["answer_key"],
    #     image_key=config["data"]["image_key"],
    #     max_prompt_length=config["data"]["max_prompt_length"],
    #     truncation="right",
    #     system_prompt=config["data"]["system_prompt"],
    #     min_pixels=config["data"]["min_pixels"],
    #     max_pixels=config["data"]["max_pixels"],
    # )
    
    

    
    config = OmegaConf.load(config_path)
    config.worker.actor.model.model_path = "Qwen/Qwen2.5-VL-3B-Instruct"
    config.data.system_prompt = """"""
    gui_r1_train_path = "/home/fsq/hf_home/hub/datasets--ritzzai--GUI-R1/snapshots/ca55ddaa180c5e8f8b27003221c391efa10a1f52/train.parquet"
    
    # instantiate tokenizer
    tokenizer = get_tokenizer(
        config.worker.actor.model.model_path,
        trust_remote_code=config.worker.actor.model.trust_remote_code,
        use_fast=True,
    )
    processor = get_processor(
        config.worker.actor.model.model_path,
        trust_remote_code=config.worker.actor.model.trust_remote_code,
        use_fast=True,
    )
    
    train_dataset = RLHFDataset(
        data_path=gui_r1_train_path,
        tokenizer=tokenizer,
        processor=processor,
        prompt_key=config.data.prompt_key,
        answer_key=config.data.answer_key,
        image_key=config.data.image_key,
        max_prompt_length=config.data.max_prompt_length,
        truncation="right",
        system_prompt=config.data.system_prompt,
        min_pixels=config.data.min_pixels,
        max_pixels=config.data.max_pixels,
    )

