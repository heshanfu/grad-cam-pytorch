#!/usr/bin/env python
# coding: utf-8
#
# Author:   Kazuto Nakashima
# URL:      http://kazuto1011.github.io
# Created:  2017-05-18

from __future__ import print_function

import copy

import click
import cv2
import numpy as np
import torch
import torch.hub
import torch.nn.functional as F
from torch.autograd import Variable
from torchvision import models, transforms

from grad_cam import BackPropagation, Deconvnet, GradCAM, GuidedBackPropagation

# if a model includes LSTM, such as in image captioning,
# torch.backends.cudnn.enabled = False


def get_device(cuda):
    cuda = cuda and torch.cuda.is_available()
    device = torch.device("cuda" if cuda else "cpu")
    if cuda:
        current_device = torch.cuda.current_device()
        print("Device:", torch.cuda.get_device_name(current_device))
    else:
        print("Device: CPU")
    return device


def get_classtable():
    classes = []
    with open("samples/synset_words.txt") as lines:
        for line in lines:
            line = line.strip().split(" ", 1)[1]
            line = line.split(", ", 1)[0].replace(" ", "_")
            classes.append(line)
    return classes


def preprocess(image_path):
    raw_image = cv2.imread(image_path)[..., ::-1]
    raw_image = cv2.resize(raw_image, (224,) * 2)
    image = transforms.Compose(
        [
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )(raw_image)
    return image, raw_image


def save_gradient(filename, gradient):
    gradient = gradient.cpu().numpy().transpose(1, 2, 0)
    gradient -= gradient.min()
    gradient /= gradient.max()
    gradient *= 255.0
    cv2.imwrite(filename, np.uint8(gradient))


def save_gradcam(filename, gcam, raw_image):
    gcam = gcam.cpu().numpy()
    gcam = cv2.applyColorMap(np.uint8(gcam * 255.0), cv2.COLORMAP_JET)
    gcam = gcam.astype(np.float) + raw_image.astype(np.float)
    gcam = gcam / gcam.max() * 255.0
    cv2.imwrite(filename, np.uint8(gcam))


# torchvision models
model_names = sorted(
    name
    for name in models.__dict__
    if name.islower() and not name.startswith("__") and callable(models.__dict__[name])
)


@click.group()
@click.pass_context
def main(ctx):
    print("Mode:", ctx.invoked_subcommand)


@main.command()
@click.option("-i", "--image-paths", type=str, multiple=True, required=True)
@click.option("-a", "--arch", type=click.Choice(model_names), required=True)
@click.option("-t", "--target-layer", type=str, required=True)
@click.option("-k", "--topk", type=int, default=3)
@click.option("--cuda/--cpu", default=True)
def demo1(image_paths, target_layer, arch, topk, cuda):
    """
    Visualize model responses given multiple images
    """

    device = get_device(cuda)

    # Synset words
    classes = get_classtable()

    # Model from torchvision
    model = models.__dict__[arch](pretrained=True)
    model.to(device)
    model.eval()

    # Images
    images = []
    raw_images = []
    print("Images:")
    for i, image_path in enumerate(image_paths):
        print("\t#{}: {}".format(i, image_path))
        image, raw_image = preprocess(image_path)
        images.append(image)
        raw_images.append(raw_image)
    images = torch.stack(images).to(device)

    """
    Common usage:
    1. Wrap your model with visualization classes defined in grad_cam.py
    2. Run forward() with images
    3. Run backward() with a list of specific classes
    4. Run generate() to export results
    """

    # =========================================================================
    print("Vanilla Backpropagation:")

    bp = BackPropagation(model=model)
    probs, ids = bp.forward(images)

    for i in range(topk):
        # In this example, we specify the high confidence classes
        bp.backward(ids=ids[:, [i]])
        gradients = bp.generate()

        # Save results as image files
        for j in range(len(images)):
            print("\t#{}: {} ({:.5f})".format(j, classes[ids[j, i]], probs[j, i]))

            save_gradient(
                filename="results/{}-{}-vanilla-{}.png".format(
                    j, arch, classes[ids[j, i]]
                ),
                gradient=gradients[j],
            )

    # Remove all the hook function in the "model"
    bp.remove_hook()

    # =========================================================================
    print("Deconvolution:")

    deconv = Deconvnet(model=model)
    _ = deconv.forward(images)

    for i in range(topk):
        deconv.backward(ids=ids[:, [i]])
        gradients = deconv.generate()

        for j in range(len(images)):
            print("\t#{}: {} ({:.5f})".format(j, classes[ids[j, i]], probs[j, i]))

            save_gradient(
                filename="results/{}-{}-deconvnet-{}.png".format(
                    j, arch, classes[ids[j, i]]
                ),
                gradient=gradients[j],
            )

    deconv.remove_hook()

    # =========================================================================
    print("Grad-CAM/Guided Backpropagation/Guided Grad-CAM:")

    gcam = GradCAM(model=model)
    _ = gcam.forward(images)

    gbp = GuidedBackPropagation(model=model)
    _ = gbp.forward(images)

    for i in range(topk):
        # Guided Backpropagation
        gbp.backward(ids=ids[:, [i]])
        gradients = gbp.generate()

        # Grad-CAM
        gcam.backward(ids=ids[:, [i]])
        regions = gcam.generate(target_layer=target_layer)

        for j in range(len(images)):
            print("\t#{}: {} ({:.5f})".format(j, classes[ids[j, i]], probs[j, i]))

            # Guided Backpropagation
            save_gradient(
                filename="results/{}-{}-guided-{}.png".format(
                    j, arch, classes[ids[j, i]]
                ),
                gradient=gradients[j],
            )

            # Grad-CAM
            save_gradcam(
                filename="results/{}-{}-gradcam-{}-{}.png".format(
                    j, arch, target_layer, classes[ids[j, i]]
                ),
                gcam=regions[j, 0],
                raw_image=raw_images[j],
            )

            # Guided Grad-CAM
            save_gradient(
                filename="results/{}-{}-guided_gradcam-{}-{}.png".format(
                    j, arch, target_layer, classes[ids[j, i]]
                ),
                gradient=torch.mul(regions, gradients)[j],
            )


@main.command()
@click.option("-i", "--image-paths", type=str, multiple=True, required=True)
@click.option("--cuda/--cpu", default=True)
def demo2(image_paths, cuda):
    """
    Generate Grad-CAM at different layers of ResNet-152
    """

    device = get_device(cuda)

    # Synset words
    classes = get_classtable()

    # Model
    model = models.resnet152(pretrained=True)
    model.to(device)
    model.eval()

    # The four residual layers
    target_layers = ["layer1", "layer2", "layer3", "layer4"]

    # Images
    images = []
    raw_images = []
    print("Images:")
    for i, image_path in enumerate(image_paths):
        print("\t#{}: {}".format(i, image_path))
        image, raw_image = preprocess(image_path)
        images.append(image)
        raw_images.append(raw_image)
    images = torch.stack(images).to(device)

    gcam = GradCAM(model=model)
    probs, ids = gcam.forward(images)
    top_ids = ids[:, [0]]
    gcam.backward(ids=top_ids)

    for target_layer in target_layers:
        print("Generating Grad-CAM @{}".format(target_layer))

        # Grad-CAM
        regions = gcam.generate(target_layer=target_layer)

        for j in range(len(images)):
            print("\t#{}: {} ({:.5f})".format(j, classes[ids[j, 0]], probs[j, 0]))

            save_gradcam(
                filename="results/{}-{}-gradcam-{}-{}.png".format(
                    j, "resnet152", target_layer, classes[top_ids[j]]
                ),
                gcam=regions[j, 0],
                raw_image=raw_images[j],
            )


@main.command()
@click.option("-i", "--image-paths", type=str, multiple=True, required=True)
@click.option("-k", "--topk", type=int, default=3)
@click.option("--cuda/--cpu", default=True)
def demo3(image_paths, topk, cuda):
    """
    Generate Grad-CAM with original models
    """

    device = get_device(cuda)

    # Synset words
    classes = get_classtable()

    # Third-party model from my other repository, e.g. Xception v1 ported from Keras
    model = torch.hub.load(
        "kazuto1011/pytorch-ported-models", "xception_v1", pretrained=True
    )
    model.to(device)
    model.eval()

    # Check available layer names
    print("Layers:")
    for m in model.named_modules():
        print("\t", m[0])

    # Here we choose the last convolution layer
    target_layer = "exit_flow.conv4"

    # Preprocessing
    def _preprocess(image_path):
        raw_image = cv2.imread(image_path)[..., ::-1]
        raw_image = cv2.resize(raw_image, model.image_shape)
        image = torch.FloatTensor(raw_image)
        image -= model.mean
        image /= model.std
        image = image.permute(2, 0, 1)
        return image, raw_image

    # Images
    images = []
    raw_images = []
    print("Images:")
    for i, image_path in enumerate(image_paths):
        print("\t#{}: {}".format(i, image_path))
        image, raw_image = _preprocess(image_path)
        images.append(image)
        raw_images.append(raw_image)
    images = torch.stack(images).to(device)

    print("Grad-CAM:")

    gcam = GradCAM(model=model)
    probs, ids = gcam.forward(images)

    for i in range(topk):

        # Grad-CAM
        gcam.backward(ids=ids[:, [i]])
        regions = gcam.generate(target_layer=target_layer)

        for j in range(len(images)):
            print("\t#{}: {} ({:.5f})".format(j, classes[ids[j, i]], probs[j, i]))

            # Grad-CAM
            save_gradcam(
                filename="results/{}-{}-gradcam-{}-{}.png".format(
                    j, "xception_v1", target_layer, classes[ids[j, i]]
                ),
                gcam=regions[j, 0],
                raw_image=raw_images[j],
            )


if __name__ == "__main__":
    main()
