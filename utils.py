import copy
import logging
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp
import torch
from sklearn.metrics import auc, precision_recall_curve, roc_curve
from torch import device, nn, optim, t
from torch.autograd import Variable
from torch.nn import functional as F
from torch.utils.data import DataLoader, TensorDataset, dataset
from tqdm import tqdm

import graph_function as g
from gae.model import GCNModelAE, GCNModelVAE, g_loss_function
from gae.utils import get_roc_score, mask_test_edges, preprocess_graph
from models import vae_loss


def highly_variable_genes(data, 
    layer=None, n_top_genes=None, 
    min_disp=0.5, max_disp=np.inf, min_mean=0.0125, max_mean=3, 
    span=0.3, n_bins=20, flavor='seurat', subset=False, inplace=True, batch_key=None, PCA_graph=False, PCA_dim = 50, k = 10, n_pcs=40):

    adata = sc.AnnData(data)

    adata.var_names_make_unique()  # this is unnecessary if using `var_names='gene_ids'` in `sc.read_10x_mtx`
    adata.obs_names_make_unique()


    if n_top_genes!=None:
        sc.pp.highly_variable_genes(adata,layer=layer,n_top_genes=n_top_genes,
        span=span, n_bins=n_bins, flavor='seurat_v3', subset=subset, inplace=inplace, batch_key=batch_key)

    else: 
        sc.pp.log1p(adata)
        sc.pp.highly_variable_genes(adata,
        layer=layer,n_top_genes=n_top_genes,
        min_disp=min_disp, max_disp=max_disp, min_mean=min_mean, max_mean=max_mean, 
        span=span, n_bins=n_bins, flavor=flavor, subset=subset, inplace=inplace, batch_key=batch_key)

    if PCA_graph == True:
        sc.tl.pca(adata,n_comps=PCA_dim)
        X_pca = adata.obsm["X_pca"]
        sc.pp.neighbors(adata, n_neighbors=k, n_pcs=n_pcs)

        return adata.var.highly_variable,adata,X_pca


    return adata.var.highly_variable,adata

def train_extractor_model(net,data_loaders={},optimizer=None,loss_function=None,n_epochs=100,scheduler=None,load=False,save_path="model.pkl"):
    
    if(load!=False):
        if(os.path.exists(save_path)):
            net.load_state_dict(torch.load(save_path))           
            return net, 0
        else:
            logging.warning("Failed to load existing file, proceed to the trainning process.")
    
    dataset_sizes = {x: data_loaders[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}
    loss_train = {}
    
    best_model_wts = copy.deepcopy(net.state_dict())
    best_loss = np.inf

    for epoch in range(n_epochs):
        logging.info('Epoch {}/{}'.format(epoch, n_epochs - 1))
        logging.info('-' * 10)

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                net.train()  # Set model to training mode
            else:
                net.eval()  # Set model to evaluate mode

            running_loss = 0.0

            n_iters = len(data_loaders[phase])


            # Iterate over data.
            # for data in data_loaders[phase]:
            for batchidx, (x, _) in enumerate(data_loaders[phase]):

                x.requires_grad_(True)
                # encode and decode 
                output = net(x)
                # compute loss
                loss = loss_function(output, x)      

                # zero the parameter (weight) gradients
                optimizer.zero_grad()

                # backward + optimize only if in training phase
                if phase == 'train':
                    loss.backward()
                    # update the weights
                    optimizer.step()

                # print loss statistics
                running_loss += loss.item()
            
  
            epoch_loss = running_loss / n_iters

            
            if phase == 'train':
                scheduler.step(epoch_loss)
                
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            loss_train[epoch,phase] = epoch_loss
            logging.info('{} Loss: {:.8f}. Learning rate = {}'.format(phase, epoch_loss,last_lr))
            
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss
                best_model_wts = copy.deepcopy(net.state_dict())
    
    # Select best model wts
    torch.save(best_model_wts, save_path)
    net.load_state_dict(best_model_wts)           
    
    return net, loss_train

def train_GAE_model(net,adj,data_loaders={},optimizer=None,loss_function=None,n_epochs=100,scheduler=None,load=False,save_path="model.pkl"):
    
    if(load!=False):
        if(os.path.exists(save_path)):
            net.load_state_dict(torch.load(save_path))           
            return net, 0
        else:
            logging.warning("Failed to load existing file, proceed to the trainning process.")
    
    dataset_sizes = {x: data_loaders[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}
    loss_train = {}
    
    best_model_wts = copy.deepcopy(net.state_dict())
    best_loss = np.inf

    for epoch in range(n_epochs):
        logging.info('Epoch {}/{}'.format(epoch, n_epochs - 1))
        logging.info('-' * 10)

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                net.train()  # Set model to training mode
            else:
                net.eval()  # Set model to evaluate mode

            running_loss = 0.0

            n_iters = len(data_loaders[phase])


            # Iterate over data.
            # for data in data_loaders[phase]:
            for batchidx, (x, _) in enumerate(data_loaders[phase]):

                x.requires_grad_(True)
                # encode and decode 
                output = net(x,adj)
                # compute loss
                loss = loss_function(output, x)      

                # zero the parameter (weight) gradients
                optimizer.zero_grad()

                # backward + optimize only if in training phase
                if phase == 'train':
                    loss.backward()
                    # update the weights
                    optimizer.step()

                # print loss statistics
                running_loss += loss.item()

            epoch_loss = running_loss / n_iters

            
            if phase == 'train':
                scheduler.step(epoch_loss)
                
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            loss_train[epoch,phase] = epoch_loss
            logging.info('{} Loss: {:.8f}. Learning rate = {}'.format(phase, epoch_loss,last_lr))
            
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss
                best_model_wts = copy.deepcopy(net.state_dict())
    
    # Select best model wts
    torch.save(best_model_wts, save_path)
    net.load_state_dict(best_model_wts)           
    
    return net, loss_train

def train_VAE_model(net,data_loaders={},optimizer=None,n_epochs=100,scheduler=None,load=False,save_path="model.pkl",best_model_cache = "drive"):
    
    if(load!=False):
        if(os.path.exists(save_path)):
            net.load_state_dict(torch.load(save_path))           
            return net, 0
        else:
            logging.warning("Failed to load existing file, proceed to the trainning process.")
    
    dataset_sizes = {x: data_loaders[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}
    loss_train = {}
    
    if best_model_cache == "memory":
        best_model_wts = copy.deepcopy(net.state_dict())
    else:
        torch.save(net.state_dict(), save_path+"_bestcahce.pkl")
    
    best_loss = np.inf

    for epoch in range(n_epochs):
        logging.info('Epoch {}/{}'.format(epoch, n_epochs - 1))
        logging.info('-' * 10)

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                net.train()  # Set model to training mode
            else:
                net.eval()  # Set model to evaluate mode

            running_loss = 0.0

            n_iters = len(data_loaders[phase])


            # Iterate over data.
            # for data in data_loaders[phase]:
            for batchidx, (x, _) in enumerate(data_loaders[phase]):

                x.requires_grad_(True)
                # encode and decode 
                output = net(x)
                # compute loss

                #losses = net.loss_function(*output, M_N=data_loaders[phase].batch_size/dataset_sizes[phase])      
                #loss = losses["loss"]

                recon_loss = nn.MSELoss(reduction="sum")

                loss = vae_loss(output[0],output[1],output[2],output[3],recon_loss,data_loaders[phase].batch_size/dataset_sizes[phase])

                # zero the parameter (weight) gradients
                optimizer.zero_grad()

                # backward + optimize only if in training phase
                if phase == 'train':
                    loss.backward()
                    # update the weights
                    optimizer.step()

                # print loss statistics
                running_loss += loss.item()
            
                
            epoch_loss = running_loss / dataset_sizes[phase]
            #epoch_loss = running_loss / n_iters

            
            if phase == 'train':
                scheduler.step(epoch_loss)
                
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            loss_train[epoch,phase] = epoch_loss
            logging.info('{} Loss: {:.8f}. Learning rate = {}'.format(phase, epoch_loss,last_lr))
            
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss

                if best_model_cache == "memory":
                    best_model_wts = copy.deepcopy(net.state_dict())
                else:
                    torch.save(net.state_dict(), save_path+"_bestcahce.pkl")
    
    # Select best model wts if use memory to cahce models
    if best_model_cache == "memory":
        torch.save(best_model_wts, save_path)
        net.load_state_dict(best_model_wts)  
    else:
        net.load_state_dict((torch.load(save_path+"_bestcahce.pkl")))
        torch.save(net.state_dict(), save_path)

    return net, loss_train

def train_predictor_model(net,data_loaders,optimizer,loss_function,n_epochs,scheduler,load=False,save_path="model.pkl"):

    if(load!=False):
        if(os.path.exists(save_path)):
            net.load_state_dict(torch.load(save_path))           
            return net, 0
        else:
            logging.warning("Failed to load existing file, proceed to the trainning process.")
    
    dataset_sizes = {x: data_loaders[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}
    loss_train = {}
    
    best_model_wts = copy.deepcopy(net.state_dict())
    best_loss = np.inf



    for epoch in range(n_epochs):
        logging.info('Epoch {}/{}'.format(epoch, n_epochs - 1))
        logging.info('-' * 10)


        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                net.train()  # Set model to training mode
            else:
                net.eval()  # Set model to evaluate mode

            running_loss = 0.0

            # N iter s calculated
            n_iters = len(data_loaders[phase])

            # Iterate over data.
            # for data in data_loaders[phase]:
            for batchidx, (x, y) in enumerate(data_loaders[phase]):

                x.requires_grad_(True)
                # encode and decode 
                output = net(x)
                # compute loss
                loss = loss_function(output, y)      

                # zero the parameter (weight) gradients
                optimizer.zero_grad()

                # backward + optimize only if in training phase
                if phase == 'train':
                    loss.backward()
                    # update the weights
                    optimizer.step()

                # print loss statistics
                running_loss += loss.item()
            

            epoch_loss = running_loss / n_iters

            if phase == 'train':
                scheduler.step(epoch_loss)
                
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            loss_train[epoch,phase] = epoch_loss
            logging.info('{} Loss: {:.8f}. Learning rate = {}'.format(phase, epoch_loss,last_lr))
            
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss
                best_model_wts = copy.deepcopy(net.state_dict())
    
    # Select best model wts
        torch.save(best_model_wts, save_path)
        
    net.load_state_dict(best_model_wts)           
    
    return net, loss_train

def train_transfer_model(
    source_encoder, target_encoder, discriminator,
    source_loader, target_loader,
    dis_loss, target_loss,
    optimizer, d_optimizer,
    scheduler,d_scheduler,
    n_epochs,device,save_path="saved/models/model.pkl",
    args=None):
    
    
    target_dataset_sizes = {x: target_loader[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}
    source_dataset_sizes = {x: source_loader[x].dataset.tensors[0].shape[0] for x in ['train', 'val']}

    dataset_sizes = {x: min(target_dataset_sizes[x],source_dataset_sizes[x]) for x in ['train', 'val']}

    loss_train = {}
    loss_d_train = {}

    for epoch in range(n_epochs):
        logging.info('Epoch {}/{}'.format(epoch, n_epochs - 1))
        logging.info('-' * 10)

        # Each epoch has a training and validation phase
        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                source_encoder.eval()
                target_encoder.train()  # Set model to training mode
                discriminator.train()  # Set model to training mode

            else:
                source_encoder.eval()
                target_encoder.eval()  # Set model to evaluate mode
                discriminator.eval()  # Set model to training mode

            running_loss = 0.0
            d_running_loss = 0.0

                #losses, d_losses = AverageMeter(), AverageMeter()
            n_iters = min(len(source_loader[phase]), len(target_loader[phase]))
            source_iter, target_iter = iter(source_loader[phase]), iter(target_loader[phase])

            # Iterate over data.
            # for data in data_loaders[phase]:
            for iter_i in range(n_iters):
                source_data, source_target = source_iter.next()
                target_data, target_target = target_iter.next()
                source_data = source_data.to(device)
                target_data = target_data.to(device)
                s_bs = source_data.size(0)
                t_bs = target_data.size(0)

                D_input_source = source_encoder.encode(source_data)
                D_input_target = target_encoder.encode(target_data)
                D_target_source = torch.tensor(
                    [0] * s_bs, dtype=torch.long).to(device)
                D_target_target = torch.tensor(
                    [1] * t_bs, dtype=torch.long).to(device)

                # Add adversarial label    
                D_target_adversarial = torch.tensor(
                    [0] * t_bs, dtype=torch.long).to(device)
                
                # train Discriminator
                # Please fix it here to be a classifier
                D_output_source = discriminator(D_input_source)
                D_output_target = discriminator(D_input_target)
                D_output = torch.cat([D_output_source, D_output_target], dim=0)
                D_target = torch.cat([D_target_source, D_target_target], dim=0)
                d_loss = dis_loss(D_output, D_target)


                d_optimizer.zero_grad()

                if phase == 'train':
                    d_loss.backward()
                    d_optimizer.step()
                
                d_running_loss += d_loss.item()

                D_input_target = target_encoder.encode(target_data)
                D_output_target = discriminator(D_input_target)
                loss = dis_loss(D_output_target, D_target_adversarial)

                optimizer.zero_grad()


                if phase == 'train':

                    loss.backward()
                    optimizer.step()
                
                running_loss += loss.item()
            

            epoch_loss = running_loss/n_iters
            d_epoch_loss = d_running_loss/n_iters


            if phase == 'train':
                scheduler.step(epoch_loss)
                d_scheduler.step(d_epoch_loss)
                
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            d_last_lr = d_scheduler.optimizer.param_groups[0]['lr']

            loss_train[epoch,phase] = epoch_loss
            loss_d_train[epoch,phase] = d_epoch_loss

            logging.info('Discriminator {} Loss: {:.8f}. Learning rate = {}'.format(phase, d_epoch_loss,d_last_lr))
            logging.info('Encoder {} Loss: {:.8f}. Learning rate = {}'.format(phase, epoch_loss,last_lr))

            # if phase == 'val' and epoch_loss < best_loss:
            #     best_loss = epoch_loss
            #     best_model_wts = copy.deepcopy(net.state_dict())
    
    # Select best model wts
        torch.save(discriminator.state_dict(), save_path+"_d.pkl")
        torch.save(target_encoder.state_dict(), save_path+"_te.pkl")

    #net.load_state_dict(best_model_wts)           
    
    return discriminator,target_encoder, loss_train, loss_d_train


def train_adversarial_model(
    source_encoder, target_encoder, discriminator,
    source_loader, target_loader,
    dis_loss, target_loss,
    optimizer, d_optimizer,
    args=None):
    source_encoder.eval()
    target_encoder.encoder.train()
    discriminator.train()

    #losses, d_losses = AverageMeter(), AverageMeter()
    n_iters = min(len(source_loader), len(target_loader))
    source_iter, target_iter = iter(source_loader), iter(target_loader)
    for iter_i in range(n_iters):
        source_data, source_target = source_iter.next()
        target_data, target_target = target_iter.next()
        source_data = source_data.to(args.device)
        target_data = target_data.to(args.device)
        bs = source_data.size(0)

        D_input_source = source_encoder.encoder(source_data)
        D_input_target = target_encoder.encoder(target_data)
        D_target_source = torch.tensor(
            [0] * bs, dtype=torch.long).to(args.device)
        D_target_target = torch.tensor(
            [1] * bs, dtype=torch.long).to(args.device)

        # train Discriminator
        D_output_source = discriminator(D_input_source)
        D_output_target = discriminator(D_input_target)
        D_output = torch.cat([D_output_source, D_output_target], dim=0)
        D_target = torch.cat([D_target_source, D_target_target], dim=0)
        d_loss = dis_loss(D_output, D_target)
        d_optimizer.zero_grad()
        d_loss.backward()
        d_optimizer.step()
        #d_losses.update(d_loss.item(), bs)

        # train Target
        D_input_target = target_encoder.encoder(target_data)
        D_output_target = discriminator(D_input_target)
        loss = target_loss(D_output_target, D_target_source)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        #losses.update(loss.item(), bs)
    return {'d/loss': d_losses.avg, 'target/loss': losses.avg}


def GAEpreditor(model, z,y, adj,optimizer,loss_function,n_epochs,scheduler,load=False,precisionModel='Float',save_path="model.pkl"):
    '''
    GAE embedding for clustering
    Param:
        z,adj
    Return:
        Embedding from graph
    '''
    if(load!=False):
        model.load_state_dict(torch.load(save_path))           
        return model, 0
    
    # featrues from z
    # Louvain

    # device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    # features = z

    # features = torch.FloatTensor(features).to(device)

    # Store original adjacency matrix (without diagonal entries) for later

    #adj_train, train_edges, val_edges, val_edges_false, test_edges, test_edges_false = mask_test_edges(adj)
    #adj = adj_train

    # Some preprocessing
    #adj_norm = preprocess_graph(adj)

    if precisionModel == 'Double':
        model=model.double()

    #adj_norm = torch.FloatTensor(adj_norm)
    #adj_norm.to(device)
    
    best_loss = np.inf

    for epoch in tqdm(range(n_epochs)):
        # mem=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # print('Mem consumption before training: '+str(mem))

        for phase in ['train', 'val']:
            if phase == 'train':
                #optimizer = scheduler(optimizer, epoch)
                model.train()  # Set model to training mode
            else:
                model.eval()  # Set model to evaluate mode

            optimizer.zero_grad()
            result = model(z[phase], adj[phase])

            loss = loss_function(result,y[phase])
            cur_loss = loss.item()


            if phase == 'train':
                loss.backward()
                optimizer.step()
                scheduler.step(cur_loss)
            
            last_lr = scheduler.optimizer.param_groups[0]['lr']
            ap_curr = 0


            logging.info("Epoch: {}, Phase: {}, loss_gae={:.5f}, lr={:.5f}".format(
                epoch + 1,phase, cur_loss, last_lr))

            if phase == 'val' and cur_loss < best_loss:
                best_loss = cur_loss
                best_model_wts = copy.deepcopy(model.state_dict())


    logging.info("Optimization Finished!")

    #roc_score, ap_score = get_roc_score(hidden_emb, adj_orig, test_edges, test_edges_false)
    #logging.info('Test ROC score: ' + str(roc_score))
    #logging.info('Test AP score: ' + str(ap_score))
    model.load_state_dict(best_model_wts)           
    torch.save(model.state_dict(), save_path)

 
    return model,0


def plot_label_hist(Y,save=None):

    # the histogram of the data
    n, bins, patches = plt.hist(Y, 50, density=True, facecolor='g', alpha=0.75)

    plt.xlabel('Y values')
    plt.ylabel('Probability')
    plt.title('Histogram of target')
    # plt.text(60, .025, r'$\mu=100,\ \sigma=15$')
    # plt.xlim(40, 160)
    # plt.ylim(0, 0.03)
    # plt.grid(True)
    if save == None:
        plt.show()
    else:
        plt.savefig(save)


# plot no skill and model roc curves
def plot_roc_curve(test_y,naive_probs,model_probs,title="",path="figures/roc_curve.pdf"):

    # plot naive skill roc curve
    fpr, tpr, _ = roc_curve(test_y, naive_probs)
    plt.plot(fpr, tpr, linestyle='--', label='Random')
    # plot model roc curve
    fpr, tpr, _ = roc_curve(test_y, model_probs)
    plt.plot(fpr, tpr, marker='.', label='Predition')
    # axis labels
    plt.xlabel('False Positive Rate')
    plt.ylabel('True Positive Rate')
    # show the legend
    plt.legend()
    plt.title(title)

    # show the plot
    if path == None:
        plt.show()
    else:
        plt.savefig(path)
    plt.close() 

# plot no skill and model precision-recall curves
def plot_pr_curve(test_y,model_probs,selected_label = 1,title="",path="figures/prc_curve.pdf"):
    # calculate the no skill line as the proportion of the positive class
    no_skill = len(test_y[test_y==selected_label]) / len(test_y)
    # plot the no skill precision-recall curve
    plt.plot([0, 1], [no_skill, no_skill], linestyle='--', label='Random')
    # plot model precision-recall curve
    precision, recall, _ = precision_recall_curve(test_y, model_probs)
    plt.plot(recall, precision, marker='.', label='Predition')
    # axis labels
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    # show the legend
    plt.legend()
    plt.title(title)

    # show the plot
    if path == None:
        plt.show()
    else:
        plt.savefig(path)
    plt.close() 

