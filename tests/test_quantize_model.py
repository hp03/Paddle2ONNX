#   copyright (c) 2018 paddlepaddle authors. all rights reserved.
#
# licensed under the apache license, version 2.0 (the "license");
# you may not use this file except in compliance with the license.
# you may obtain a copy of the license at
#
#     http://www.apache.org/licenses/license-2.0
#
# unless required by applicable law or agreed to in writing, software
# distributed under the license is distributed on an "as is" basis,
# without warranties or conditions of any kind, either express or implied.
# see the license for the specific language governing permissions and
# limitations under the license.
import unittest
import os
import time
import sys
import random
import math
import functools
import contextlib
import numpy as np
from PIL import Image, ImageEnhance
import paddle
import paddle.fluid as fluid
from paddle.dataset.common import download
from paddle.fluid.contrib.slim.quantization import PostTrainingQuantization
import platform

if platform.system() == "Windows":
    os.system('set no_proxy=bcebos.com')
else:
    os.system('export no_proxy=bcebos.com')

paddle.enable_static()

random.seed(0)
np.random.seed(0)

DATA_DIM = 224
THREAD = 1
BUF_SIZE = 102400
DATA_DIR = 'data/ILSVRC2012'

img_mean = np.array([0.485, 0.456, 0.406]).reshape((3, 1, 1))
img_std = np.array([0.229, 0.224, 0.225]).reshape((3, 1, 1))


def resize_short(img, target_size):
    percent = float(target_size) / min(img.size[0], img.size[1])
    resized_width = int(round(img.size[0] * percent))
    resized_height = int(round(img.size[1] * percent))
    img = img.resize((resized_width, resized_height), Image.LANCZOS)
    return img


def crop_image(img, target_size, center):
    width, height = img.size
    size = target_size
    if center == True:
        w_start = (width - size) / 2
        h_start = (height - size) / 2
    else:
        w_start = np.random.randint(0, width - size + 1)
        h_start = np.random.randint(0, height - size + 1)
    w_end = w_start + size
    h_end = h_start + size
    img = img.crop((w_start, h_start, w_end, h_end))
    return img


def process_image(sample, mode, color_jitter, rotate):
    img_path = sample[0]
    img = Image.open(img_path)
    img = resize_short(img, target_size=256)
    img = crop_image(img, target_size=DATA_DIM, center=True)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    img = np.array(img).astype('float32').transpose((2, 0, 1)) / 255
    img -= img_mean
    img /= img_std
    return img, sample[1]


def _reader_creator(file_list,
                    mode,
                    shuffle=False,
                    color_jitter=False,
                    rotate=False,
                    data_dir=DATA_DIR):
    def reader():
        with open(file_list) as flist:
            full_lines = [line.strip() for line in flist]
            if shuffle:
                np.random.shuffle(full_lines)
            lines = full_lines

            for line in lines:
                img_path, label = line.split()
                img_path = os.path.join(data_dir, img_path)
                if not os.path.exists(img_path):
                    continue
                yield img_path, int(label)

    mapper = functools.partial(
        process_image, mode=mode, color_jitter=color_jitter, rotate=rotate)

    return paddle.reader.xmap_readers(mapper, reader, THREAD, BUF_SIZE)


def val(data_dir=DATA_DIR):
    file_list = os.path.join(data_dir, 'val_list.txt')
    return _reader_creator(file_list, 'val', shuffle=False, data_dir=data_dir)


class TestPostTrainingQuantization(unittest.TestCase):
    def setUp(self):
        self.int8_download = 'int8/download'
        self.cache_folder = os.path.expanduser('~/.cache/paddle/dataset/' +
                                               self.int8_download)
        self.data_cache_folder = ''
        data_urls = []
        data_md5s = []
        if os.environ.get('DATASET') == 'full':
            data_urls.append(
                'https://paddle-inference-dist.bj.bcebos.com/int8/ILSVRC2012_img_val.tar.gz.partaa'
            )
            data_md5s.append('60f6525b0e1d127f345641d75d41f0a8')
            data_urls.append(
                'https://paddle-inference-dist.bj.bcebos.com/int8/ILSVRC2012_img_val.tar.gz.partab'
            )
            data_md5s.append('1e9f15f64e015e58d6f9ec3210ed18b5')
            self.data_cache_folder = self.download_data(data_urls, data_md5s,
                                                        "full_data", False)
        else:
            data_urls.append(
                'http://paddle-inference-dist.bj.bcebos.com/int8/calibration_test_data.tar.gz'
            )
            data_md5s.append('1b6c1c434172cca1bf9ba1e4d7a3157d')
            self.data_cache_folder = self.download_data(data_urls, data_md5s,
                                                        "small_data", False)

        # reader/decorator.py requires the relative path to the data folder
        if not os.path.exists("./data/ILSVRC2012"):
            cmd = 'rm -rf {0} && ln -s {1} {0}'.format("data",
                                                       self.data_cache_folder)
            os.system(cmd)

        self.batch_size = 1 if os.environ.get('DATASET') == 'full' else 50
        self.sample_iterations = 50 if os.environ.get(
            'DATASET') == 'full' else 2
        self.infer_iterations = 50000 if os.environ.get(
            'DATASET') == 'full' else 2

        self.int8_model = "./post_training_quantize_model/"

    def tearDown(self):
        pass

    def cache_unzipping(self, target_folder, zip_path):
        if not os.path.exists(target_folder):
            cmd = 'mkdir {0} && tar xf {1} -C {0}'.format(target_folder,
                                                          zip_path)
            os.system(cmd)

    def download_data(self, data_urls, data_md5s, folder_name, is_model=True):
        data_cache_folder = os.path.join(self.cache_folder, folder_name)
        zip_path = ''
        if os.environ.get('DATASET') == 'full':
            file_names = []
            for i in range(0, len(data_urls)):
                download(data_urls[i], self.int8_download, data_md5s[i])
                file_names.append(data_urls[i].split('/')[-1])

            zip_path = os.path.join(self.cache_folder,
                                    'full_imagenet_val.tar.gz')
            if not os.path.exists(zip_path):
                cat_command = 'cat'
                for file_name in file_names:
                    cat_command += ' ' + os.path.join(self.cache_folder,
                                                      file_name)
                cat_command += ' > ' + zip_path
                os.system(cat_command)

        if os.environ.get('DATASET') != 'full' or is_model:
            download(data_urls[0], self.int8_download, data_md5s[0])
            file_name = data_urls[0].split('/')[-1]
            zip_path = os.path.join(self.cache_folder, file_name)

        print('Data is downloaded at {0}'.format(zip_path))
        self.cache_unzipping(data_cache_folder, zip_path)
        return data_cache_folder

    def download_model(self):
        pass

    def run_program(self,
                    model_path,
                    batch_size,
                    infer_iterations,
                    model_filename="",
                    params_filename="",
                    run_onnxruntime=False):
        image_shape = [3, 224, 224]
        infer_program = None
        feed_dict = None
        fetch_targets = None

        input_name = None
        sess = None
        if run_onnxruntime:
            import onnxruntime as rt
            import paddle2onnx
            onnx_model = paddle2onnx.command.c_paddle_to_onnx(
                model_file=model_path + model_filename,
                params_file=model_path + params_filename,
                opset_version=13,
                enable_onnx_checker=True)
            sess_options = rt.SessionOptions()
            sess_options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_DISABLE_ALL
            sess = rt.InferenceSession(
                onnx_model, sess_options, providers=['CPUExecutionProvider'])
            input_name = sess.get_inputs()[0].name
            label_name = sess.get_outputs()[0].name
            print("sess input/output name : ", input_name, label_name)
        else:
            place = fluid.CPUPlace()
            exe = fluid.Executor(place)
            [infer_program, feed_dict, fetch_targets] = \
                fluid.io.load_inference_model(model_path, exe, model_filename=model_filename, params_filename=params_filename)

        val_reader = paddle.batch(val(), batch_size)
        iterations = infer_iterations

        cnt = 0
        periods = []
        results = []
        for batch_id, data in enumerate(val_reader()):
            image = np.array(
                [x[0].reshape(image_shape) for x in data]).astype("float32")
            label = np.array([x[1] for x in data]).astype("int64")
            label = label.reshape([-1, 1])

            t1 = time.time()
            if run_onnxruntime:
                pred = sess.run(None, {input_name: image})
            else:
                pred = exe.run(infer_program,
                               feed={feed_dict[0]: image},
                               fetch_list=fetch_targets)
            t2 = time.time()
            period = t2 - t1
            periods.append(period)

            pred = np.array(pred[0])
            sort_array = pred.argsort(axis=1)
            top_1_pred = sort_array[:, -1:][:, ::-1]
            top_1 = np.mean(label == top_1_pred)
            results.append(top_1)

            cnt += len(data)

            if (batch_id + 1) % 100 == 0:
                print("{0} images,".format(batch_id + 1))
                sys.stdout.flush()
            if (batch_id + 1) == iterations:
                break
        result = np.mean(np.array(results), axis=0)
        print("top1_acc = {}".format(result))
        throughput = cnt / np.sum(periods)
        latency = np.average(periods)
        acc1 = result
        return (throughput, latency, acc1)

    def generate_quantized_model(self,
                                 model_path,
                                 quantizable_op_type,
                                 algo="mse",
                                 round_type="round",
                                 is_full_quantize=False,
                                 is_use_cache_file=False,
                                 is_optimize_model=False,
                                 onnx_format=False,
                                 model_filename="__model__",
                                 params_filename="__params__"):
        try:
            os.system("mkdir " + self.int8_model)
        except Exception as e:
            print("Failed to create {} due to {}".format(self.int8_model,
                                                         str(e)))
            sys.exit(-1)

        place = fluid.CPUPlace()
        exe = fluid.Executor(place)
        scope = fluid.global_scope()
        val_reader = val()

        ptq = PostTrainingQuantization(
            executor=exe,
            sample_generator=val_reader,
            model_dir=model_path,
            model_filename=model_filename,
            params_filename=params_filename,
            algo=algo,
            quantizable_op_type=quantizable_op_type,
            round_type=round_type,
            is_full_quantize=is_full_quantize,
            optimize_model=is_optimize_model,
            onnx_format=onnx_format,
            is_use_cache_file=is_use_cache_file)
        ptq.quantize()
        ptq.save_quantized_model(
            self.int8_model,
            model_filename='__model__',
            params_filename='__params__')

    def run_test(self,
                 model,
                 algo,
                 round_type,
                 data_urls,
                 data_md5s,
                 quantizable_op_type,
                 is_full_quantize,
                 is_use_cache_file,
                 is_optimize_model,
                 diff_threshold,
                 onnx_format=False):
        infer_iterations = self.infer_iterations
        batch_size = self.batch_size
        sample_iterations = self.sample_iterations

        model_cache_folder = self.download_data(data_urls, data_md5s, model)

        print("Start FP32 inference for {0} on {1} images ...".format(
            model, infer_iterations * batch_size))
        (fp32_throughput, fp32_latency, fp32_acc1) = self.run_program(
            model_cache_folder, batch_size, infer_iterations,
            "inference.pdmodel", "inference.pdiparams")

        print("Start INT8 post training quantization for {0} on {1} images ...".
              format(model, sample_iterations * batch_size))
        self.generate_quantized_model(
            model_cache_folder, quantizable_op_type, algo, round_type,
            is_full_quantize, is_use_cache_file, is_optimize_model, onnx_format,
            "inference.pdmodel", "inference.pdiparams")

        print("Start INT8 inference for {0} on {1} images ...".format(
            model, infer_iterations * batch_size))
        (int8_throughput, int8_latency, int8_acc1) = self.run_program(
            self.int8_model, batch_size, infer_iterations, "__model__",
            "__params__")

        print("Start use ONNXRuntime inference for {0} on {1} images ...".
              format(model, infer_iterations * batch_size))
        (onnx_int8_throughput, onnx_int8_latency,
         onnx_int8_acc1) = self.run_program(
             self.int8_model,
             batch_size,
             infer_iterations,
             "__model__",
             "__params__",
             run_onnxruntime=True)

        print("---Post training quantization of {} method---".format(algo))
        print(
            "FP32 {0}: batch_size {1}, throughput {2} images/second, latency {3} second, accuracy {4}.".
            format(model, batch_size, fp32_throughput, fp32_latency, fp32_acc1))
        print(
            "INT8 {0}: batch_size {1}, throughput {2} images/second, latency {3} second, accuracy {4}.".
            format(model, batch_size, int8_throughput, int8_latency, int8_acc1))
        print(
            "ONNXRuntime INT8 {0}: batch_size {1}, throughput {2} images/second, latency {3} second, accuracy {4}.\n".
            format(model, batch_size, onnx_int8_throughput, onnx_int8_latency,
                   onnx_int8_acc1))
        sys.stdout.flush()

        delta_value = fp32_acc1 - int8_acc1
        self.assertLess(delta_value, diff_threshold)
        delta_value = int8_acc1 - onnx_int8_acc1
        self.assertLess(delta_value, diff_threshold)


class TestPostTrainingMseONNXFormatForMobilenetv1(TestPostTrainingQuantization):
    def test_post_training_mse_onnx_format_mobilenetv1(self):
        model = "MobileNetV1_infer"
        algo = "mse"
        round_type = "round"
        data_urls = [
            'https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/inference/MobileNetV1_infer.tar'
        ]
        data_md5s = ['5ee2b1775b11dc233079236cdc216c2e']
        quantizable_op_type = [
            "conv2d",
            "depthwise_conv2d",
            "mul",
        ]
        is_full_quantize = True
        is_use_cache_file = False
        is_optimize_model = False
        onnx_format = True
        diff_threshold = 0.05
        self.run_test(
            model,
            algo,
            round_type,
            data_urls,
            data_md5s,
            quantizable_op_type,
            is_full_quantize,
            is_use_cache_file,
            is_optimize_model,
            diff_threshold,
            onnx_format=onnx_format)


class TestPostTrainingHistKlAvgONNXFormatForMobilenetv1(
        TestPostTrainingQuantization):
    def test_post_training_hist_kl_avg_onnx_format_mobilenetv1(self):
        model = "MobileNetV1_infer"
        algos = ["hist", "KL", "avg"]
        algo = np.random.choice(algos)
        round_type = "round"
        data_urls = [
            'https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/inference/MobileNetV1_infer.tar'
        ]
        data_md5s = ['5ee2b1775b11dc233079236cdc216c2e']
        quantizable_op_type = [
            "conv2d",
            "depthwise_conv2d",
            "mul",
        ]
        is_full_quantize = True
        is_use_cache_file = False
        is_optimize_model = False
        onnx_format = True
        diff_threshold = 0.08
        self.run_test(
            model,
            algo,
            round_type,
            data_urls,
            data_md5s,
            quantizable_op_type,
            is_full_quantize,
            is_use_cache_file,
            is_optimize_model,
            diff_threshold,
            onnx_format=onnx_format)


class TestPostTrainingMseONNXFormatForResnet50(TestPostTrainingQuantization):
    def test_post_training_mse_onnx_format_resnet50(self):
        model = "ResNet50_infer"
        algo = "mse"
        round_type = "round"
        data_urls = [
            'https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/inference/ResNet50_infer.tar'
        ]
        data_md5s = ['8b5e4dc0c1b12635e7f299c24038a451']
        quantizable_op_type = ["conv2d", "mul"]
        is_full_quantize = True
        is_use_cache_file = False
        is_optimize_model = False
        diff_threshold = 0.05
        onnx_format = True
        self.run_test(
            model,
            algo,
            round_type,
            data_urls,
            data_md5s,
            quantizable_op_type,
            is_full_quantize,
            is_use_cache_file,
            is_optimize_model,
            diff_threshold,
            onnx_format=onnx_format)


class TestPostTrainingHistKlAvgONNXFormatForResnet50(
        TestPostTrainingQuantization):
    def test_post_training_hist_kl_avg_onnx_format_resnet50(self):
        model = "ResNet50_infer"
        algos = ["hist", "KL", "avg"]
        algo = np.random.choice(algos)
        round_type = "round"
        data_urls = [
            'https://paddle-imagenet-models-name.bj.bcebos.com/dygraph/inference/ResNet50_infer.tar'
        ]
        data_md5s = ['8b5e4dc0c1b12635e7f299c24038a451']
        quantizable_op_type = ["conv2d", "mul"]
        is_full_quantize = True
        is_use_cache_file = False
        is_optimize_model = False
        diff_threshold = 0.05
        onnx_format = True
        self.run_test(
            model,
            algo,
            round_type,
            data_urls,
            data_md5s,
            quantizable_op_type,
            is_full_quantize,
            is_use_cache_file,
            is_optimize_model,
            diff_threshold,
            onnx_format=onnx_format)


if __name__ == '__main__':
    unittest.main()