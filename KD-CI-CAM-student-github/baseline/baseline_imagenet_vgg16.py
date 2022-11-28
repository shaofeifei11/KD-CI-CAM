import numpy as np
from torch import nn
import torch
import config as cfg
from utils import create_rois, get_pre_two_source_inds, compute_gt_rois
class Self_Attn(nn.Module):
    def __init__(self, in_dim):
        super(Self_Attn, self).__init__()
        self.channel_in = in_dim
        # self.activation = activation

        self.query_conv = nn.Conv2d(in_channels = in_dim, out_channels = in_dim // 8, kernel_size = 1)
        self.key_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim // 8, kernel_size=1)
        self.value_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim, kernel_size=1)
        self.mix_conv = nn.Conv2d(in_channels=in_dim, out_channels=in_dim, kernel_size=1)
        # self.mix_conv.weight.data.fill_(0.0)
        # self.mix_conv.bias.data.fill_(0.0)
        self.softmax = nn.Softmax(dim=-1)
        self.bn = nn.BatchNorm2d(in_dim)
        nn.init.constant_(self.bn.weight, 0.0)
        nn.init.constant_(self.bn.bias, 0.0)
        self.relu = nn.ReLU()

    def forward(self, x):
        m_batchsize, C, width, height = x.size()
        proj_query = self.query_conv(x).view(m_batchsize, -1, width*height).permute(0,2,1)
        proj_key = self.key_conv(x).view(m_batchsize, -1, width*height)
        energy = torch.bmm(proj_query, proj_key)
        attention = self.softmax(energy)
        proj_value = self.value_conv(x).view(m_batchsize, -1, width*height)

        out = torch.bmm(proj_value, attention.permute(0,2,1))
        out = out.view(m_batchsize, C, width, height)
        # out = self.relu(self.bn(self.mix_conv(out)))
        out = (self.bn(self.mix_conv(out)))
        out = out + x
        # print(self.gamma)
        return out

class Non_Local_VGG16_ImageNet(nn.Module):
    def __init__(self, pretrain=True):
        super(Non_Local_VGG16_ImageNet, self).__init__()
        self.conv = nn.Sequential(
            # 3 x 128 x 128
            nn.Conv2d(3, 64, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(64, 64, 3, 1, 1), nn.LeakyReLU(0.2),
            # nn.BatchNorm2d(64),
            nn.MaxPool2d(2, 2),

            # 32 x 128 x 128
            nn.Conv2d(64, 128, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(128, 128, 3, 1, 1), nn.LeakyReLU(0.2),
            # nn.BatchNorm2d(128),
            nn.MaxPool2d(2, 2),
            # Self_Attn(128),
            # 64 x 128 x 128
            nn.Conv2d(128, 256, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(256, 256, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(256, 256, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.MaxPool2d(2, 2),
            Self_Attn(256),
            # 64 x 64 x 64
            nn.Conv2d(256, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(512, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(512, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.MaxPool2d(2, 2),
            Self_Attn(512),
            # 128 x 64 x 64
            nn.Conv2d(512, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(512, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(512, 512, 3, 1, 1), nn.LeakyReLU(0.2),
            # nn.MaxPool2d(2, 2),
            Self_Attn(512),
            nn.Conv2d(512, 1024, 3, 1, 1), nn.LeakyReLU(0.2),
            nn.Conv2d(1024, 1024, 3, 1, 1), nn.LeakyReLU(0.2)
        )
        if pretrain:
            self.weights_pretrain()
            # self.weights_init()
            print('pretrained weight load complete..')

    def weights_pretrain(self):
        """
        :return:
        """
        pretrained_weights = torch.load('pre_train/vgg16_imgnet.pth')
        pretrained_list = pretrained_weights.keys()
        for i, layer_name in enumerate(pretrained_list):
            layer_num = int(layer_name.split('.')[1])
            layer_group = layer_name.split('.')[0]
            layer_type = layer_name.split('.')[-1]

            # if layer_num >= 10:
            #     layer_num = layer_num + 1
            if layer_num >= 17:
                layer_num = layer_num + 1
            if layer_num >= 24:
                layer_num = layer_num + 1

            if layer_group != "features":
                break

            if layer_type == 'weight':
                assert self.conv[layer_num].weight.data.size() == pretrained_weights[
                    layer_name].size(), "size error!"
                self.conv[layer_num].weight.data = pretrained_weights[layer_name]
            else:  # layer type == 'bias'
                assert self.conv[layer_num].bias.size() == pretrained_weights[layer_name].size(), "size error!"
                self.conv[layer_num].bias.data = pretrained_weights[layer_name]

    def forward(self, x):
        return self.conv(x)

class ImageNet_VGG16_Baseline(nn.Module):
    def __init__(self, args):
        super(ImageNet_VGG16_Baseline, self).__init__()
        self.num_classes = args.num_classes
        self.args = args

        self.backbone = Non_Local_VGG16_ImageNet(cfg.pretrain)

        self.gap = nn.AvgPool2d(cfg.attention_size)

        # up branch
        self.up_classifier = nn.Sequential(
            nn.Linear(1024, self.num_classes),
        )

        self.upsample = nn.Upsample(scale_factor=cfg.total_stride, mode='bilinear')

    def forward(self, x):

        feature_map = self.backbone(x)
        ############ up ###############
        self.up_feature_map = feature_map
        up_vector = self.gap(self.up_feature_map).view(self.up_feature_map.size(0), -1)
        self.up_out = self.up_classifier(up_vector)
        self.pred_sort_up, self.pred_ids_up = torch.sort(self.up_out, dim=-1, descending=True)
        self.up_cam = self._compute_cam(self.up_feature_map, self.up_classifier[0].weight)

        return self.up_cam, self.up_out, self.pred_sort_up, self.pred_ids_up

    def update_pred_ids(self, pred_ids_up):
        """
        :param up:
        :param down:
        :return:
        """
        self.pred_ids_up = pred_ids_up
        return self.pred_ids_up

    def _compute_cam(self, input, weight):
        """
        :param input:
        :param weight:
        :return:
        """
        input = input.permute(1, 0, 2, 3)
        nc, bz, h, w = input.shape
        input = input.reshape((nc, bz * h * w))
        cams = torch.matmul(weight, input)
        cams = cams.reshape(self.num_classes, bz, h, w)
        cams = cams.permute(1, 0, 2, 3)
        return cams

    def compute_rois_up(self, seg_thr, topk, combination):
        """
        :param pred:  [batch_size, classes]
        :param cam:  [batch_size, channel, height, width]
        :return:
        """
        return create_rois(self.pred_sort_up, self.pred_ids_up, self.up_cam, self.upsample, seg_thr=seg_thr, topk=topk,
                           combination=combination, function=cfg.function, mean_num=cfg.mean_num)  # [batch, topk, 4]

    def compute_gt_rois_up(self, seg_thr, labels, combination):
        """
        :param pred:  [batch_size, classes]
        :param cam:  [batch_size, channel, height, width]
        :return:
        """
        return compute_gt_rois(self.pred_sort_up, self.pred_ids_up, self.up_cam, self.upsample, seg_thr=seg_thr,
                               labels=labels,
                               combination=combination, function=cfg.function, mean_num=cfg.mean_num)  # [batch, 4]
